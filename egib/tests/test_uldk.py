"""Działka z ULDK po identyfikatorze — odpowiedź usługi podstawiona (bez sieci, dane fikcyjne)."""
import pytest
from sophia_plugin_egib import uldk

from app.fetchers import FetchError, NotFound


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class FakeClient:
    def __init__(self, text, status=200):
        self.resp = FakeResp(text, status)
        self.params = None

    def get(self, url, params):
        self.params = params
        return self.resp


def test_valid_id():
    assert uldk.valid_id("999901_2.0001.12/1")
    assert uldk.valid_id("999901_1.0001.AR_1.98/12")
    assert not uldk.valid_id("12/1")
    assert not uldk.valid_id("999901.0001.12")


def test_fetch_by_id_parses_and_normalizes():
    body = "0\n999901_2.0001.12/1|przykładowe|przykładowy|Przykładowo|Wieś|12/1|SRID=2180;POLYGON((0 0,0 10,10 10,0 0))\n"
    c = FakeClient(body)
    props, ewkt = uldk.fetch_by_id("999901_2.0001.12/1", c)
    assert c.params["request"] == "GetParcelById" and c.params["id"] == "999901_2.0001.12/1"
    assert c.params["result"].endswith("geom_wkt") and c.params["srid"] == "2180"
    assert props["teryt"] == "999901_2.0001.12/1" and props["numer"] == "12/1" and props["obreb"] == "Wieś"
    assert props["obreb_nr"] == "0001" and props["gmina_teryt"] == "999901_2"
    assert ewkt.startswith("SRID=2180;POLYGON")


def test_fetch_by_id_errors():
    with pytest.raises(NotFound):
        uldk.fetch_by_id("999901_2.0001.13", FakeClient("-1 brak wyników\n"))
    county = "-1 brak wyników\nbłędny format odpowiedzi XML, usługa zwróciła odpowiedź…\n"
    with pytest.raises(FetchError) as e:          # awaria usługi powiatu — nie „brak działki”
        uldk.fetch_by_id("999901_2.0001.13", FakeClient(county))
    assert not isinstance(e.value, NotFound)
    with pytest.raises(FetchError):
        uldk.fetch_by_id("999901_2.0001.13", FakeClient("", status=500))
    other = "0\n999901_2.0001.99|w|p|g|o|99|SRID=2180;POLYGON((0 0,0 1,1 1,0 0))\n"
    with pytest.raises(FetchError):              # ULDK zwróciło inną działkę
        uldk.fetch_by_id("999901_2.0001.13", FakeClient(other))
