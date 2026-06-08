"""SIGHUP hot-reload of ollama-sentinel.yaml (OP-1).

A long-running `ollama-sentinel run` loads its YAML once at start. These
tests pin the in-place reload behavior: model/timeout updates are applied by
rebuilding the OllamaClient without restarting the watcher; watch.directory
changes are warned-and-skipped; a broken YAML leaves the running config intact.
"""
import asyncio
import logging
import os
import signal

import pytest
import yaml

from ollama_sentinel.watcher import FileSentinel


def _rewrite_yaml(path, mutate):
    """Load the YAML, apply mutate(dict) in place, write it back."""
    data = yaml.safe_load(path.read_text())
    mutate(data)
    path.write_text(yaml.dump(data, sort_keys=False))


class TestReloadConfig:
    async def test_reload_updates_request_timeout(self, config_yaml_path):
        sentinel = FileSentinel(config_yaml_path)
        try:
            assert sentinel.processor.ollama_client.client.timeout.read == 120.0
            _rewrite_yaml(
                config_yaml_path,
                lambda d: d["ollama"].__setitem__("request_timeout", 600),
            )
            result = await sentinel.reload_config()
            assert result is True
            assert sentinel.config.ollama.request_timeout == 600
            assert sentinel.processor.ollama_client.client.timeout.read == 600.0
        finally:
            await sentinel.processor.close()

    async def test_reload_updates_default_model(self, config_yaml_path):
        sentinel = FileSentinel(config_yaml_path)
        try:
            _rewrite_yaml(
                config_yaml_path,
                lambda d: d["ollama"]["models"]["default"].__setitem__("name", "new-model"),
            )
            await sentinel.reload_config()
            assert sentinel.config.ollama.models["default"].name == "new-model"
            assert (
                sentinel.processor.ollama_client.config["models"]["default"]["name"]
                == "new-model"
            )
        finally:
            await sentinel.processor.close()

    async def test_reload_closes_old_client(self, config_yaml_path):
        sentinel = FileSentinel(config_yaml_path)
        old_client = sentinel.processor.ollama_client
        try:
            await sentinel.reload_config()
            assert sentinel.processor.ollama_client is not old_client
            assert old_client.client.is_closed
        finally:
            await sentinel.processor.close()

    async def test_reload_ignores_directory_change(self, config_yaml_path, tmp_path, caplog):
        sentinel = FileSentinel(config_yaml_path)
        original_dir = sentinel.config.watch.directory
        try:
            other = tmp_path / "elsewhere"
            other.mkdir()
            _rewrite_yaml(
                config_yaml_path,
                lambda d: d["watch"].__setitem__("directory", str(other)),
            )
            with caplog.at_level(logging.WARNING):
                await sentinel.reload_config()
            assert sentinel.config.watch.directory == original_dir
            assert any("directory" in r.message.lower() for r in caplog.records)
        finally:
            await sentinel.processor.close()

    async def test_reload_keeps_config_on_load_failure(self, config_yaml_path):
        sentinel = FileSentinel(config_yaml_path)
        try:
            config_yaml_path.write_text("not: valid: yaml: {[}")
            result = await sentinel.reload_config()
            assert result is False
            assert sentinel.config.ollama.request_timeout == 120
        finally:
            await sentinel.processor.close()


class TestSighupHandler:
    async def test_sighup_triggers_reload(self, config_yaml_path):
        if not hasattr(signal, "SIGHUP"):
            pytest.skip("SIGHUP not available on this platform")
        sentinel = FileSentinel(config_yaml_path)
        loop = asyncio.get_running_loop()
        try:
            sentinel.install_reload_handler(loop)
            _rewrite_yaml(
                config_yaml_path,
                lambda d: d["ollama"].__setitem__("request_timeout", 600),
            )
            os.kill(os.getpid(), signal.SIGHUP)
            for _ in range(100):
                await asyncio.sleep(0.02)
                if sentinel.config.ollama.request_timeout == 600:
                    break
            assert sentinel.config.ollama.request_timeout == 600
        finally:
            loop.remove_signal_handler(signal.SIGHUP)
            await sentinel.processor.close()
