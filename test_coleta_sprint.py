"""Testes das funcoes puras de coleta_sprint_4h.py (sem chamadas de rede)."""

from coleta_sprint_4h import (
    normalizar_youtube_target,
    normalizar_reddit_target,
    normalizar_instagram_target,
)


class TestNormalizarYoutubeTarget:
    def test_id_cru_passa_direto(self):
        assert normalizar_youtube_target("UC1Nm7gQCcGvgLyVcGTXp-Ww") == "UC1Nm7gQCcGvgLyVcGTXp-Ww"

    def test_url_channel_extrai_id(self):
        url = "https://www.youtube.com/channel/UC1Nm7gQCcGvgLyVcGTXp-Ww"
        assert normalizar_youtube_target(url) == "UC1Nm7gQCcGvgLyVcGTXp-Ww"

    def test_url_channel_com_barra_final(self):
        url = "https://www.youtube.com/channel/UC1Nm7gQCcGvgLyVcGTXp-Ww/"
        assert normalizar_youtube_target(url) == "UC1Nm7gQCcGvgLyVcGTXp-Ww"

    def test_url_handle_vira_handle_com_arroba(self):
        url = "https://www.youtube.com/@somechannel"
        assert normalizar_youtube_target(url) == "@somechannel"

    def test_handle_cru_sem_arroba_ganha_arroba(self):
        assert normalizar_youtube_target("somechannel") == "@somechannel"

    def test_handle_cru_com_arroba_mantem(self):
        assert normalizar_youtube_target("@somechannel") == "@somechannel"


class TestNormalizarRedditTarget:
    def test_nome_cru(self):
        assert normalizar_reddit_target("python") == "python"

    def test_prefixo_r_barra(self):
        assert normalizar_reddit_target("r/python") == "python"

    def test_url_completa(self):
        assert normalizar_reddit_target("https://www.reddit.com/r/python/") == "python"

    def test_url_sem_www(self):
        assert normalizar_reddit_target("https://reddit.com/r/dataisbeautiful") == "dataisbeautiful"


class TestNormalizarInstagramTarget:
    def test_username_cru(self):
        assert normalizar_instagram_target("nasa") == "nasa"

    def test_com_arroba(self):
        assert normalizar_instagram_target("@nasa") == "nasa"

    def test_url_completa(self):
        assert normalizar_instagram_target("https://www.instagram.com/nasa/") == "nasa"

    def test_url_sem_www_sem_barra_final(self):
        assert normalizar_instagram_target("https://instagram.com/natgeo") == "natgeo"
