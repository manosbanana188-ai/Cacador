"""Interpreta pedidos em português ("dentistas sem site em Curitiba, 15 km")
e devolve os parâmetros da busca, usando a API da Anthropic (tool use)."""
from __future__ import annotations

import json
from typing import Optional

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-5-5"

SITE_MAP = {
    "sem_site": "Sem site",
    "so_rede_social": "Só rede social/diretório",
    "com_site": "Com site",
}

TOOL = {
    "name": "definir_busca",
    "description": "Define os parâmetros da busca de clientes e a resposta ao usuário.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": "Resposta curta em português do Brasil: o que você entendeu, ou a pergunta que falta.",
            },
            "ready": {
                "type": "boolean",
                "description": "true só se já há ao menos um nicho e um local e o usuário quer buscar agora.",
            },
            "niches": {"type": "array", "items": {"type": "string"},
                       "description": "Profissões/negócios a buscar, no singular (ex.: dentista)."},
            "location": {"type": "string", "description": "Cidade, endereço, estado ou país."},
            "area_type": {"type": "string", "enum": ["radius", "region"],
                          "description": "radius = cidade/endereço com raio em km; region = estado ou país inteiro."},
            "radius_km": {"type": "number", "description": "Raio em km (1 a 300). Só para area_type=radius."},
            "site_filter": {"type": "array", "items": {"type": "string", "enum": list(SITE_MAP)},
                            "description": "Quais situações de site mostrar."},
            "min_rating": {"type": "number"},
            "min_reviews": {"type": "integer"},
            "only_phone": {"type": "boolean"},
        },
        "required": ["reply", "ready"],
    },
}

SYSTEM = """Você é o assistente de um caçador de clientes que busca negócios locais no Google
e identifica quem não tem site. Converta o pedido do usuário em parâmetros chamando a ferramenta
definir_busca. Regras:
- Preencha só o que o usuário pediu ou mudou; o resto fica como está nas configurações atuais.
- Se faltar nicho ou local, ready=false e pergunte de forma curta na resposta.
- Pedidos de follow-up ("agora só os sem site", "aumenta pra 30 km") alteram as configurações atuais.
- Se o usuário falar de um estado ou país inteiro, area_type=region; senão radius (padrão 10 km).
- "Sem site" significa site_filter=["sem_site"]; "sem site ou só instagram" inclui "so_rede_social".
- Nunca invente telefones, nomes ou resultados: você só configura a busca.
Configurações atuais: {current}"""


class AIError(RuntimeError):
    pass


def parse_request(
    api_key: str,
    history: list[dict],
    text: str,
    current: dict,
    *,
    model: str = MODEL,
    timeout: float = 45.0,
) -> dict:
    """history: [{'role': 'user'|'assistant', 'content': str}] (turnos anteriores)."""
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-8:]]
    messages.append({"role": "user", "content": text})
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 600,
            "system": SYSTEM.format(current=json.dumps(current, ensure_ascii=False)),
            "messages": messages,
            "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": TOOL["name"]},
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        try:
            msg = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            msg = resp.text
        raise AIError(f"Anthropic respondeu {resp.status_code}: {msg}")
    for block in resp.json().get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == TOOL["name"]:
            return normalize(block.get("input", {}))
    raise AIError("A IA não devolveu os parâmetros da busca.")


def normalize(raw: dict) -> dict:
    out: dict = {"reply": str(raw.get("reply", "")).strip(), "ready": bool(raw.get("ready", False))}
    niches = [str(n).strip() for n in raw.get("niches") or [] if str(n).strip()]
    if niches:
        out["niches"] = niches
    if str(raw.get("location", "")).strip():
        out["location"] = str(raw["location"]).strip()
    if raw.get("area_type") in ("radius", "region"):
        out["area_type"] = raw["area_type"]
    if isinstance(raw.get("radius_km"), (int, float)):
        out["radius_km"] = int(min(max(raw["radius_km"], 1), 300))
    sf = [SITE_MAP[s] for s in raw.get("site_filter") or [] if s in SITE_MAP]
    if sf:
        out["statuses"] = sf
    if isinstance(raw.get("min_rating"), (int, float)):
        out["min_rating"] = float(min(max(raw["min_rating"], 0), 5))
    if isinstance(raw.get("min_reviews"), (int, float)):
        out["min_reviews"] = int(max(raw["min_reviews"], 0))
    if isinstance(raw.get("only_phone"), bool):
        out["only_phone"] = raw["only_phone"]
    return out
