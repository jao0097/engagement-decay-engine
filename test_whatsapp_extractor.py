"""Testes de whatsapp_extractor.py. Rodar com: pytest test_whatsapp_extractor.py -v"""

import pandas as pd

from whatsapp_extractor import parse_whatsapp_export, is_system_message, make_event_id, build_whatsapp_events


class TestParseWhatsappExport:
    def test_mensagem_simples(self):
        texto = "12/08/2026 22:10 - Joao Silva: oi pessoal, tudo bem?"
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 1
        assert resultado[0]["author"] == "Joao Silva"
        assert resultado[0]["text"] == "oi pessoal, tudo bem?"
        assert resultado[0]["timestamp_raw"] == "12/08/2026 22:10"

    def test_mensagem_multilinha_concatena_na_anterior(self):
        texto = "12/08/2026 22:10 - Joao Silva: primeira linha\nsegunda linha sem prefixo"
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 1
        assert resultado[0]["text"] == "primeira linha\nsegunda linha sem prefixo"

    def test_duas_mensagens_distintas(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: primeira\n"
            "12/08/2026 22:11 - Maria Souza: segunda"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[1]["author"] == "Maria Souza"

    def test_mensagem_de_sistema_sem_dois_pontos_e_ignorada(self):
        texto = (
            "12/08/2026 22:00 - As mensagens e as ligacoes agora sao protegidas "
            "com criptografia de ponta a ponta.\n"
            "12/08/2026 22:10 - Joao Silva: oi"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 1
        assert resultado[0]["author"] == "Joao Silva"

    def test_linha_vazia_ignorada(self):
        texto = "12/08/2026 22:10 - Joao Silva: oi\n\n"
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 1

    def test_arquivo_vazio(self):
        assert parse_whatsapp_export("") == []

    def test_notificacao_de_entrada_apos_mensagem_nao_e_concatenada(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: oi pessoal\n"
            "12/08/2026 22:11 - Bruno entrou usando o link de convite deste grupo\n"
            "12/08/2026 22:12 - Maria Souza: tudo bem?"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[0]["author"] == "Joao Silva"
        assert resultado[0]["text"] == "oi pessoal"
        assert resultado[1]["author"] == "Maria Souza"

    def test_notificacao_de_saida_apos_mensagem_nao_e_concatenada(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: oi pessoal\n"
            "12/08/2026 22:11 - Carla saiu\n"
            "12/08/2026 22:12 - Maria Souza: tudo bem?"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[0]["text"] == "oi pessoal"

    def test_notificacao_de_adicionado_nao_e_concatenada(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: oi pessoal\n"
            "12/08/2026 22:11 - Ana adicionou Carla\n"
            "12/08/2026 22:12 - Maria Souza: tudo bem?"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[0]["text"] == "oi pessoal"

    def test_notificacao_de_removido_nao_e_concatenada(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: oi pessoal\n"
            "12/08/2026 22:11 - Ana removeu Bruno\n"
            "12/08/2026 22:12 - Maria Souza: tudo bem?"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[0]["text"] == "oi pessoal"

    def test_notificacao_mudanca_de_nome_com_dois_pontos_nao_cria_autor_fantasma(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: oi pessoal\n"
            '12/08/2026 22:11 - Ana mudou o nome do grupo para: "Novo Nome"\n'
            "12/08/2026 22:12 - Maria Souza: tudo bem?"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[0]["text"] == "oi pessoal"
        assert resultado[1]["author"] == "Maria Souza"
        autores = {m["author"] for m in resultado}
        assert "Ana mudou o nome do grupo para" not in autores

    def test_notificacao_mudanca_de_descricao_nao_e_concatenada(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: oi pessoal\n"
            "12/08/2026 22:11 - Ana mudou a descricao do grupo\n"
            "12/08/2026 22:12 - Maria Souza: tudo bem?"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[0]["text"] == "oi pessoal"

    def test_notificacao_criptografia_apos_mensagem_nao_e_concatenada(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: oi pessoal\n"
            "12/08/2026 22:11 - As mensagens e as ligacoes agora sao protegidas "
            "com a criptografia de ponta a ponta.\n"
            "12/08/2026 22:12 - Maria Souza: tudo bem?"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[0]["text"] == "oi pessoal"

    def test_continuacao_real_sem_prefixo_de_timestamp_ainda_concatena(self):
        texto = (
            "12/08/2026 22:10 - Joao Silva: primeira linha\n"
            "segunda linha sem prefixo de timestamp\n"
            "12/08/2026 22:12 - Maria Souza: outra mensagem"
        )
        resultado = parse_whatsapp_export(texto)
        assert len(resultado) == 2
        assert resultado[0]["text"] == "primeira linha\nsegunda linha sem prefixo de timestamp"


class TestIsSystemMessage:
    def test_midia_oculta(self):
        assert is_system_message("<Midia oculta>") is True

    def test_figurinha(self):
        assert is_system_message("figurinha omitida") is True

    def test_mensagem_normal_nao_e_sistema(self):
        assert is_system_message("gostei muito da explicacao, obrigado!") is False


class TestMakeEventId:
    def test_determinismo(self):
        a = make_event_id("Joao", "2026-08-12T22:10:00+00:00", "oi")
        b = make_event_id("Joao", "2026-08-12T22:10:00+00:00", "oi")
        assert a == b

    def test_inputs_diferentes_geram_ids_diferentes(self):
        a = make_event_id("Joao", "2026-08-12T22:10:00+00:00", "oi")
        b = make_event_id("Maria", "2026-08-12T22:10:00+00:00", "oi")
        assert a != b


class TestBuildWhatsappEvents:
    def test_filtra_mensagem_de_sistema(self):
        mensagens = [
            {"author": "Joao", "timestamp_raw": "12/08/2026 22:10", "text": "<Midia oculta>"},
            {"author": "Joao", "timestamp_raw": "12/08/2026 22:11", "text": "boa explicacao, ajudou bastante!"},
        ]
        resultado = build_whatsapp_events(mensagens, grupo="Grupo Teste")
        assert len(resultado) == 1
        assert resultado.iloc[0]["platform"] == "whatsapp"
        assert resultado.iloc[0]["content_id"] == "Grupo Teste"
        assert resultado.iloc[0]["author_id"] == "Joao"

    def test_lista_vazia_apos_filtro_retorna_dataframe_com_schema_universal(self):
        mensagens = [{"author": "Joao", "timestamp_raw": "12/08/2026 22:10", "text": "<Midia oculta>"}]
        resultado = build_whatsapp_events(mensagens, grupo="Grupo Teste")
        assert resultado.empty
        assert list(resultado.columns) == [
            "event_id", "platform", "author_id", "author_display_name",
            "content_id", "published_at", "quality_score", "categorias",
        ]

    def test_quality_score_no_intervalo(self):
        mensagens = [
            {"author": "Joao", "timestamp_raw": "12/08/2026 22:10",
             "text": "Sera que da pra explicar melhor esse ponto? Fiquei com duvida."},
        ]
        resultado = build_whatsapp_events(mensagens, grupo="Grupo Teste")
        assert 0.0 <= resultado.iloc[0]["quality_score"] <= 1.0

    def test_published_at_e_iso_utc(self):
        mensagens = [{"author": "Joao", "timestamp_raw": "12/08/2026 22:10", "text": "boa pergunta, obrigado!"}]
        resultado = build_whatsapp_events(mensagens, grupo="Grupo Teste")
        assert resultado.iloc[0]["published_at"].startswith("2026-08-12T22:10:00")
