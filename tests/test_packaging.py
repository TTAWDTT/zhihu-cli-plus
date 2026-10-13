"""Installed distribution identity and compatibility across the package rename."""

from importlib.metadata import distribution

from click.testing import CliRunner

from zhihu_cli import __version__
from zhihu_cli.cli import cli


def test_independent_distribution_keeps_cli_and_python_namespace():
    package = distribution("zhihu-cli-plus")
    assert package.version == __version__
    assert package.metadata["Name"] == "zhihu-cli-plus"
    scripts = {
        entry.name: entry.value
        for entry in package.entry_points
        if entry.group == "console_scripts"
    }
    assert scripts["zhihu"] == "zhihu_cli.cli:cli"
    assert not any(req.startswith("pyzhihu-cli") for req in package.requires or [])


def test_version_output_identifies_plus():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"zhihu-cli-plus, version {__version__}"
