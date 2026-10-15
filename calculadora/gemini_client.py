"""Cliente HTTP compartilhado para a API Gemini (relatório + chat)."""

from __future__ import annotations

import json
import os
import re
import time

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Ordem: modelos estáveis primeiro; 2.5-flash como rede de segurança se a família 3.x estiver em 503
GEMINI_MODELOS_PADRAO = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
_GEMINI_RETRIES_503 = 2
_GEMINI_ESPERA_503_S = 4.0


def gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def gemini_modelos() -> list[str]:
    env_modelo = os.environ.get("GEMINI_MODEL", "").strip()
    if env_modelo:
        return [env_modelo] + [m for m in GEMINI_MODELOS_PADRAO if m != env_modelo]
    return list(GEMINI_MODELOS_PADRAO)


def reparar_json_texto(texto: str) -> str:
    """Corrige falhas comuns da IA (vírgula sobrando, cercas, comentários)."""
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)
    texto = re.sub(r",(\s*[}\]])", r"\1", texto)
    texto = re.sub(r"(?m)^\s*//.*?$", "", texto)
    return texto.strip()


def extrair_json(texto: str) -> dict:
    bruto = reparar_json_texto(texto)
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    if inicio < 0 or fim < 0 or fim <= inicio:
        raise ValueError("resposta da IA sem JSON")
    pedaco = bruto[inicio : fim + 1]
    try:
        return json.loads(pedaco)
    except json.JSONDecodeError:
        pedaco2 = re.sub(r",(\s*[}\]])", r"\1", pedaco)
        return json.loads(pedaco2)


def _post_gemini(
    client: httpx.Client,
    *,
    modelo: str,
    corpo: dict,
    api_key: str,
) -> httpx.Response | None:
    resp = None
    for tentativa in range(_GEMINI_RETRIES_503 + 1):
        try:
            resp = client.post(
                GEMINI_URL.format(model=modelo),
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=corpo,
            )
        except Exception:
            return None
        if resp.status_code == 503 and tentativa < _GEMINI_RETRIES_503:
            time.sleep(_GEMINI_ESPERA_503_S * (tentativa + 1))
            continue
        break
    return resp


def _texto_candidato(payload: dict) -> str:
    partes = payload["candidates"][0]["content"]["parts"]
    texto = "".join(p.get("text", "") for p in partes if "text" in p)
    if not texto.strip():
        raise ValueError("resposta vazia (só thinking?)")
    return texto


def chamar_gemini(
    prompt: str,
    *,
    api_key: str,
    modelos: list[str] | None = None,
    timeout: float = 240.0,
) -> tuple[dict, str]:
    """Chamada com ``responseMimeType=application/json`` (relatório)."""
    modelos = modelos or gemini_modelos()
    erros: list[str] = []
    with httpx.Client(timeout=timeout) as client:
        for modelo in modelos:
            variantes = [True, False] if "gemini-3" in modelo else [False, True]
            ok_http = False
            for com_thinking in variantes:
                corpo = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "maxOutputTokens": 8192,
                        **(
                            {"thinkingConfig": {"thinkingLevel": "low"}}
                            if com_thinking
                            else {}
                        ),
                    },
                }
                resp = _post_gemini(client, modelo=modelo, corpo=corpo, api_key=api_key)
                if resp is None:
                    erros.append(f"{modelo}: falha de rede")
                    break
                if resp.status_code == 400 and com_thinking:
                    continue
                if resp.status_code != 200:
                    erros.append(f"{modelo}: HTTP {resp.status_code} {resp.text[:220]}")
                    break
                ok_http = True
                try:
                    texto = _texto_candidato(resp.json())
                    return extrair_json(texto), modelo
                except Exception as exc:  # noqa: BLE001
                    erros.append(f"{modelo}: {exc}")
                    break
            if not ok_http:
                continue
    raise RuntimeError("; ".join(erros) or "nenhum modelo disponível")


def chamar_gemini_texto(
    prompt: str,
    *,
    api_key: str | None = None,
    modelos: list[str] | None = None,
    timeout: float = 120.0,
    max_output_tokens: int = 4096,
) -> tuple[str, str]:
    """Chamada em texto livre (chat). Devolve (resposta, modelo)."""
    key = (api_key or gemini_api_key()).strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY não configurada")
    modelos = modelos or gemini_modelos()
    erros: list[str] = []
    with httpx.Client(timeout=timeout) as client:
        for modelo in modelos:
            variantes = [True, False] if "gemini-3" in modelo else [False, True]
            ok_http = False
            for com_thinking in variantes:
                corpo = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_output_tokens,
                        **(
                            {"thinkingConfig": {"thinkingLevel": "low"}}
                            if com_thinking
                            else {}
                        ),
                    },
                }
                resp = _post_gemini(client, modelo=modelo, corpo=corpo, api_key=key)
                if resp is None:
                    erros.append(f"{modelo}: falha de rede")
                    break
                if resp.status_code == 400 and com_thinking:
                    continue
                if resp.status_code != 200:
                    erros.append(f"{modelo}: HTTP {resp.status_code} {resp.text[:220]}")
                    break
                ok_http = True
                try:
                    return _texto_candidato(resp.json()).strip(), modelo
                except Exception as exc:  # noqa: BLE001
                    erros.append(f"{modelo}: {exc}")
                    break
            if not ok_http:
                continue
    raise RuntimeError("; ".join(erros) or "nenhum modelo disponível")
