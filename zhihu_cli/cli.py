"""CLI entry point for zhihu-cli."""

from __future__ import annotations

import logging

import click

from . import __version__
from .commands.auth import login, logout, status, whoami
from .commands.comments import comment, comment_detail, delete_comment, like_comment, replies, reply
from .commands.content import answer, answers, comments, feed, feeds, hot, question, search, topic
from .commands.interact import (
    article,
    ask,
    collections,
    delete_article_cmd,
    delete_pin,
    delete_question,
    follow_question,
    notifications,
    pin,
    vote,
)
from .commands.user import followers, following, user, user_answers, user_articles
from .commands.workflows import WORKFLOW_COMMANDS


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option(version=__version__, prog_name="zhihu-cli-plus")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def cli(verbose: bool):
    """zhihu-cli-plus — Zhihu from your terminal."""
    _setup_logging(verbose)


# Auth
cli.add_command(login)
cli.add_command(logout)
cli.add_command(status)
cli.add_command(whoami)

# Content
cli.add_command(search)
cli.add_command(hot)
cli.add_command(question)
cli.add_command(answers)
cli.add_command(answer)
cli.add_command(comments)
cli.add_command(feed)
cli.add_command(feeds)
cli.add_command(topic)

# User
cli.add_command(user)
cli.add_command(user_answers)
cli.add_command(user_articles)
cli.add_command(followers)
cli.add_command(following)

# Interactions
cli.add_command(vote)
cli.add_command(follow_question)
cli.add_command(ask)
cli.add_command(pin)
cli.add_command(article)
cli.add_command(delete_question)
cli.add_command(delete_pin)
cli.add_command(delete_article_cmd)
cli.add_command(collections)
cli.add_command(notifications)
cli.add_command(comment)
cli.add_command(reply)
cli.add_command(comment_detail)
cli.add_command(replies)
cli.add_command(delete_comment)
cli.add_command(like_comment)
for workflow_command in WORKFLOW_COMMANDS:
    cli.add_command(workflow_command)


if __name__ == "__main__":
    cli()
