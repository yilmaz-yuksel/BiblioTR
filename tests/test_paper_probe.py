"""paper/scripts/01_probe_queries.py için testler (ağ erişimi olmadan)."""

import importlib.util
import sys
from pathlib import Path

from bibliotr.client import SearchPage

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("probe", ROOT / "paper" / "scripts" / "01_probe_queries.py")
probe = importlib.util.module_from_spec(spec)
sys.modules["probe"] = probe
spec.loader.exec_module(probe)


def test_field_probe_with_fake_client(tmp_path):
    counts = {probe.T_FA: 517, probe.T_PA: 836, probe._field("title", probe.FA, probe.PA): 860,
              probe._field("title", probe.FA, probe.PA, brackets=True): 860,
              probe._field("title", "fiziksel", "physical"): 4553, probe.K_FA: 325,
              probe._field("title", "fiziksel aktivitenin"): 44,
              probe._field("title", probe.FA, "fiziksel aktivitenin"): 561,
              f"{probe.T_FA} | {probe.K_FA}": 120, f"{probe.T_FA} OR {probe.K_FA}": 0,
              probe.FA: 9000, "gamify": 26596}

    class Fake:
        def search(self, entity, q, order, page, limit):
            if q == "gamify":
                raise RuntimeError("ağ hatası")
            return SearchPage("publication", 1, 10, counts[q], [], {}, {})

    totals = probe.run_field(Fake(), tmp_path)
    assert totals["gamify"] is None and totals[probe.T_FA] == 517
    text = (tmp_path / "field.txt").read_text(encoding="utf-8")
    assert "VEYA çalışıyor" in text and "(aynı)" in text
    assert "temel ifade ekli biçimi eşleştirmiyor" in text
    assert "'|' ile: başlık 517, anahtar kelime 325 -> 120 (kesişim)" in text
    assert "'OR' ile: başlık 517, anahtar kelime 325 -> 0 (hiç kayıt yok" in text
    assert (tmp_path / "counts.csv").exists() and (tmp_path / "probe.json").exists()


def test_main_defaults_to_field_probe(tmp_path, monkeypatch):
    """`--set` verilmese de, `--set fa_alan` verilse de alan yoklaması çalışır."""
    calls = []
    monkeypatch.setattr(probe, "TRDizinClient", lambda min_interval: object())
    monkeypatch.setattr(probe, "run_field", lambda client, outdir: calls.append(outdir))
    probe.main(["--out", str(tmp_path / "a")])
    probe.main(["--set", "fa_alan", "--out", str(tmp_path / "b")])
    assert calls == [tmp_path / "a", tmp_path / "b"]
