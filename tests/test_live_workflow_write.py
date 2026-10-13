"""Explicitly approved, bounded writes on owned content and temporary resources."""

from __future__ import annotations

import collections
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from tests.test_live import run_json
from zhihu_cli.action_requests import (
    build_answer_delete_request,
    build_collection_request,
    build_like_request,
)
from zhihu_cli.auth import cookie_str_to_dict, get_saved_cookie_string
from zhihu_cli.client import ZhihuClient
from zhihu_cli.comment_requests import numeric_id
from zhihu_cli.display import strip_html
from zhihu_cli.exceptions import DataFetchError
from zhihu_cli.pagination import collect_pages
from zhihu_cli.workflow_client import action_object

pytestmark = [pytest.mark.integration, pytest.mark.integration_workflow_write]


def lifecycle_texts(marker):
    return (
        f"zhihu-cli-plus 临时测试（{marker}）：验证回答发布，测试完成后删除。",
        f"zhihu-cli-plus 临时测试（{marker}）：验证回答修改，测试完成后删除。",
    )


class WriteGuard:
    def __init__(self, original, *, article_id, pin_id, question_id, marker):
        self.original = original
        self.article = numeric_id(article_id)
        self.pin = numeric_id(pin_id)
        self.question = numeric_id(question_id)
        self.marker = marker
        self.title = f"cli测试-{marker.removeprefix('cli-plus-wave-')}"
        self.collection = None
        self.answer = None
        self.old_collections = set()
        self.old_answers = set()
        self.counts = collections.Counter()
        self.ready = False
        self.last = 0.0
        self.allowed_steps = None
        self.responses = []

    def bind(self, kind, resource_id):
        resource_id = numeric_id(resource_id)
        baseline = self.old_collections if kind == "collection" else self.old_answers
        if resource_id in baseline:
            raise AssertionError("A created resource unexpectedly matches an existing resource")
        setattr(self, kind, resource_id)

    def __call__(self, session, method, *args, **kwargs):
        method = method.upper()
        url = args[0] if args else kwargs["url"]
        parsed = urlsplit(url)
        key = None
        if method != "GET":
            if not self.ready or parsed.scheme != "https" or parsed.netloc != "www.zhihu.com":
                raise AssertionError("Only approved HTTPS writes after preflight are allowed")
            body = json.loads(kwargs["data"]) if kwargs.get("data") else None
            query = parse_qs(parsed.query)
            path = parsed.path
            if method == "POST" and path == "/api/v4/collections" and not query:
                assert body == {
                    "title": self.title,
                    "description": "临时接口实测，完成后删除。",
                    "is_public": False,
                }
                key = "create_collection"
            elif self.collection and path == f"/api/v4/collections/{self.collection}/contents":
                assert query == {"content_id": [self.article], "content_type": ["article"]}
                assert body is None and method in ("POST", "DELETE")
                key = "add_item" if method == "POST" else "remove_item"
            elif (
                method == "DELETE"
                and self.collection
                and path == f"/api/v4/collections/{self.collection}"
            ):
                assert body is None and not query
                key = "delete_collection"
            elif method == "POST" and path == f"/api/v4/articles/{self.article}/voters":
                assert body in ({"voting": 1}, {"voting": 0}) and not query
                key = "like_article" if body["voting"] == 1 else "unlike_article"
            elif path == f"/api/v4/pins/{self.pin}/voters/up":
                assert method in ("POST", "DELETE") and body is None and not query
                key = "like_pin" if method == "POST" else "unlike_pin"
            elif method == "POST" and path == "/api/v4/content/publish":
                assert body["action"] == "answer" and not query
                data = body["data"]
                texts = lifecycle_texts(self.marker)
                content = strip_html(data["hybrid"]["html"]).strip()
                if content == texts[0]:
                    assert data["extra_info"]["question_id"] == self.question
                    assert data["draft"]["isPublished"] is False
                    key = "publish_answer"
                else:
                    assert content == texts[1] and self.answer
                    assert data["draft"]["contentId"] == self.answer
                    assert data["draft"]["isPublished"] is True
                    key = "edit_answer"
            elif method == "DELETE" and self.answer and path == f"/api/v4/answers/{self.answer}":
                assert not query and body is None
                key = "delete_answer"
            if (
                key is None
                or self.counts[key]
                or (self.allowed_steps is not None and key not in self.allowed_steps)
            ):
                raise AssertionError("An unrelated or repeated write was blocked before HTTP")
            self.counts[key] += 1
        time.sleep(max(0.0, 0.4 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        response = self.original(session, method, *args, **kwargs)
        if key:
            diagnostic = {"step": key, "status": response.status_code}
            try:
                raw = response.json()
                diagnostic["keys"] = sorted(raw) if isinstance(raw, dict) else []
                if isinstance(raw, dict):
                    error = raw.get("error")
                    if isinstance(error, dict):
                        diagnostic["error"] = {
                            k: error[k] for k in ("code", "message") if k in error
                        }
                    diagnostic.update(
                        {k: raw[k] for k in ("success", "voting", "is_liked") if k in raw}
                    )
                    diagnostic.update(
                        {k: raw[k] for k in ("code", "message", "toast_message") if k in raw}
                    )
                    data = raw.get("data")
                    if isinstance(data, dict):
                        diagnostic["data_keys"] = sorted(data)
                        diagnostic["data_status"] = {
                            k: data[k]
                            for k in ("write", "publishType", "code", "message")
                            if k in data
                        }
                        obj = action_object(raw)
                        diagnostic["result_keys"] = sorted(obj)
                        diagnostic["result_status"] = {
                            k: obj[k]
                            for k in ("id", "content_id", "code", "message", "error")
                            if k in obj
                        }
            except (ValueError, DataFetchError):
                diagnostic["json"] = False
            self.responses.append(diagnostic)
        if key in ("create_collection", "publish_answer") and response.status_code in (200, 201):
            try:
                data = action_object(response.json())
                rid = data.get("id") or data.get("content_id")
                if rid:
                    self.bind("collection" if key == "create_collection" else "answer", rid)
            except (ValueError, AttributeError, DataFetchError):
                pass
        return response


def enable_guard(monkeypatch, request):
    if not request.config.getoption("--live-workflow-write"):
        pytest.skip("Explicit workflow-write approval is required")
    options = [
        request.config.getoption(name)
        for name in (
            "--live-workflow-article-id",
            "--live-workflow-pin-id",
            "--live-workflow-question-id",
            "--live-workflow-marker",
        )
    ]
    if not all(options):
        pytest.fail("Specific owned article, pin, question and marker are required")
    guard = WriteGuard(
        requests.sessions.Session.request,
        article_id=options[0],
        pin_id=options[1],
        question_id=options[2],
        marker=options[3],
    )
    scopes = {
        "test_approved_collection_and_cleanup": {
            "create_collection",
            "add_item",
            "remove_item",
            "delete_collection",
        },
        "test_approved_likes_and_restoration": {
            "like_article",
            "unlike_article",
            "like_pin",
            "unlike_pin",
        },
        "test_approved_answer_and_cleanup": {"publish_answer", "edit_answer", "delete_answer"},
    }
    guard.allowed_steps = scopes[request.node.originalname]
    request.node.workflow_write_guard = guard

    def guarded(session, method, *args, **kwargs):
        return guard(session, method, *args, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "request", guarded)


def _all(client, fetch):
    return collect_pages(client, fetch, all_pages=True, max_pages=20)["data"]


def _liked(obj, kind):
    if kind == "article" and obj.get("voting") in (0, 1):
        return bool(obj["voting"])
    value = obj.get("is_liked", obj.get("virtuals", {}).get("is_liked"))
    assert value in (False, True), "Like state is not available"
    return bool(value)


def _wait(fetch, predicate):
    for _ in range(6):
        data = fetch()
        if predicate(data):
            return data
        time.sleep(1)
    raise AssertionError("The accepted operation did not read back as expected")


def _absent(fetch):
    try:
        fetch()
    except DataFetchError as error:
        if "status 404" in str(error):
            return True
        raise
    return False


def _preflight(client, guard):
    me = client.get_self_info()
    token = me["url_token"]
    questions = _all(client, lambda: client.get_user_items("questions", token))
    assert guard.question in {str(q["id"]) for q in questions}
    collections = _all(client, lambda: client.get_user_items("collections", token))
    answers = _all(client, lambda: client.get_user_answers(token))
    guard.old_collections = {str(c["id"]) for c in collections}
    guard.old_answers = {str(a["id"]) for a in answers}
    assert not any(str(a.get("question", {}).get("id")) == guard.question for a in answers)
    assert not client.get_draft_versions(guard.question, "question")["data"]
    article, pin = client.get_article(guard.article), client.get_pin(guard.pin)
    likes = any(s.startswith("like_") for s in guard.allowed_steps)
    for detail in (article, pin):
        author_id = detail.get("author", {}).get("id")
        assert author_id
        if likes:
            assert str(author_id) != str(me["id"]), "Zhihu prohibits liking your own content"
        else:
            assert str(author_id) == str(me["id"])
    assert _liked(article, "article") is False and _liked(pin, "pin") is False
    guard.ready = True
    return token


def _client():
    cookie = get_saved_cookie_string()
    assert cookie, "A complete local login is required; credentials are not printed"
    return ZhihuClient(cookie_str_to_dict(cookie))


def _record(request, guard, failures):
    request.node.user_properties.extend(
        [
            ("marker", guard.marker),
            ("created_collection_id", guard.collection or ""),
            ("created_answer_id", guard.answer or ""),
            ("writes", json.dumps(dict(guard.counts))),
            ("responses", json.dumps(guard.responses, ensure_ascii=False)),
            ("cleanup_passed", str(not failures)),
        ]
    )
    if failures:
        pytest.fail("Temporary-resource cleanup needs attention: " + "; ".join(failures))


def test_approved_collection_and_cleanup(request):
    guard = request.node.workflow_write_guard
    with _client() as client:
        token = _preflight(client, guard)
        try:
            result = run_json(
                ["create-collection", guard.title, "--description", "临时接口实测，完成后删除。"]
            )
            guard.bind("collection", result["resource_id"])
            detail = client.get_collection(guard.collection)
            obj = detail.get("collection", detail)
            assert obj["title"] == guard.title and obj["is_public"] is False
            run_json(["collect", guard.collection, guard.article, "--type", "article"])
            items = client.get_collection_items(guard.collection)["data"]
            assert any(str(x.get("content", x).get("id")) == guard.article for x in items)
            run_json(["collect", guard.collection, guard.article, "--type", "article", "--undo"])
            assert not client.get_collection_items(guard.collection)["data"]
        finally:
            failures = []
            try:
                if guard.counts["create_collection"] and not guard.collection:
                    for obj in _all(client, lambda: client.get_user_items("collections", token)):
                        if (
                            obj.get("title") == guard.title
                            and str(obj["id"]) not in guard.old_collections
                        ):
                            guard.bind("collection", obj["id"])
                if guard.collection:
                    client.execute_action(build_collection_request("delete", guard.collection))
                    assert _absent(lambda: client.get_collection(guard.collection))
            except Exception as error:
                failures.append(f"collection {guard.collection}: {type(error).__name__}: {error}")
            _record(request, guard, failures)


@pytest.mark.parametrize("kind", ["article", "pin"])
def test_approved_likes_and_restoration(request, kind):
    guard = request.node.workflow_write_guard
    guard.allowed_steps = {"like_" + kind, "unlike_" + kind}
    rid = getattr(guard, kind)
    with _client() as client:
        _preflight(client, guard)
        try:
            run_json(["like-" + kind, rid])
            _wait(
                lambda: getattr(client, "get_" + kind)(rid), lambda obj: _liked(obj, kind) is True
            )
            run_json(["like-" + kind, rid, "--undo"])
            _wait(
                lambda: getattr(client, "get_" + kind)(rid), lambda obj: _liked(obj, kind) is False
            )
        finally:
            failures = []
            try:
                if guard.counts["like_" + kind] and not guard.counts["unlike_" + kind]:
                    client.execute_action(build_like_request(rid, kind=kind, undo=True))
                assert _liked(getattr(client, "get_" + kind)(rid), kind) is False
            except Exception as error:
                failures.append(f"restore {kind}: {type(error).__name__}: {error}")
            _record(request, guard, failures)


def test_approved_answer_and_cleanup(request):
    guard = request.node.workflow_write_guard
    with _client() as client:
        token = _preflight(client, guard)
        try:
            texts = lifecycle_texts(guard.marker)
            result = run_json(["publish-answer", guard.question, texts[0]])
            guard.bind("answer", result["resource_id"])
            _wait(
                lambda: client.get_answer(guard.answer),
                lambda obj: strip_html(obj["content"]).strip() == texts[0],
            )
            time.sleep(3)
            run_json(["edit-answer", guard.answer, texts[1]])
            _wait(
                lambda: client.get_answer(guard.answer),
                lambda obj: strip_html(obj["content"]).strip() == texts[1],
            )
        finally:
            failures = []
            try:
                if guard.counts["publish_answer"] and not guard.answer:
                    for obj in _all(client, lambda: client.get_user_answers(token)):
                        if str(obj["id"]) not in guard.old_answers and guard.marker in strip_html(
                            obj.get("content", "")
                        ):
                            guard.bind("answer", obj["id"])
                if guard.answer:
                    client.execute_action(build_answer_delete_request(guard.answer))
                    assert _absent(lambda: client.get_answer(guard.answer))
                    assert not client.get_draft_versions(guard.question, "question")["data"]
            except Exception as error:
                failures.append(f"answer {guard.answer}: {type(error).__name__}: {error}")
            _record(request, guard, failures)
