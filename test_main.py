"""
Testes do parsing de argumentos do pipeline principal.
Rodar com: pytest test_main.py -v
"""

import main as m


def test_parse_args_sem_argumentos_usa_canal_default():
    channel_id, reclassify = m._parse_args([])
    assert channel_id == m.DEFAULT_CHANNEL_ID
    assert reclassify is False


def test_parse_args_com_canal_explicito():
    channel_id, reclassify = m._parse_args(["UCabc123"])
    assert channel_id == "UCabc123"
    assert reclassify is False


def test_parse_args_com_reclassify():
    channel_id, reclassify = m._parse_args(["UCabc123", "--reclassify"])
    assert channel_id == "UCabc123"
    assert reclassify is True


def test_parse_args_reclassify_sem_canal_usa_default():
    channel_id, reclassify = m._parse_args(["--reclassify"])
    assert channel_id == m.DEFAULT_CHANNEL_ID
    assert reclassify is True
