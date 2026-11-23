"""Interface do Caçador de Clientes. Rode com: streamlit run app.py"""
import hmac
import io
import os

import pandas as pd
import streamlit as st

import ai
import leadhunter as lh

st.set_page_config(page_title="Caçador de Clientes", page_icon="🎯", layout="wide")


def secret(name: str, default: str = "") -> str:
    """Lê de st.secrets (deploy) ou de variável de ambiente (local)."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name, default)


# ----------------------------------------------------------- senha (opcional)
APP_PASSWORD = secret("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("auth"):
    st.title("🎯 Caçador de Clientes")
    pw = st.text_input("Senha de acesso", type="password")
    if pw:
        if hmac.compare_digest(pw.encode(), APP_PASSWORD.encode()):
            st.session_state["auth"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()

# ----------------------------------------------------------- estado inicial
DEFAULTS = {
    "niches_raw": "advogado\ndentista\nterapeuta",
    "location": "",
    "mode_label": "Cidade/endereço + raio em km",
    "radius_km": 10,
    "statuses": [lh.SITE_NONE, lh.SITE_SOCIAL],
    "min_rating": 0.0,
    "min_reviews": 0,
    "only_phone": False,
    "chat": [],
    "run_now": False,
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

MODE_LABELS = ["Cidade/endereço + raio em km", "Estado ou país inteiro"]

# ----------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Configuração")
    google_key = secret("GOOGLE_MAPS_API_KEY") or st.text_input(
        "Chave da Google Places API", type="password",
        help="Ative a 'Places API (New)' no Google Cloud e crie uma chave.",
    )
    anthropic_key = secret("ANTHROPIC_API_KEY") or st.text_input(
        "Chave da Anthropic (para o chat de IA)", type="password",
        help="Opcional: sem ela use os campos manuais.",
    )
    max_requests = st.number_input(
        "Limite de requisições por busca", 1, 5000, 200, step=10,
        help="Trava de custo. Cada página de 20 resultados conta como 1 requisição.",
    )
    max_pages = st.select_slider(
        "Páginas por busca", options=[1, 2, 3], value=3,
        help="Cada página traz até 20 negócios; o Google entrega no máximo 60 por busca.",
    )
    only_operational = st.checkbox("Só negócios em funcionamento", value=True)
    if st.button("Limpar conversa"):
        st.session_state["chat"] = []
        st.rerun()

# ----------------------------------------------------------- chat (processado antes dos widgets)
st.title("🎯 Caçador de Clientes")
st.caption("Diga quem você procura e onde. Ex.: *dentistas sem site num raio de 15 km de Curitiba*.")

prompt = st.chat_input("O que você quer caçar?")
if prompt:
    st.session_state["chat"].append({"role": "user", "content": prompt})
    if not anthropic_key:
        st.session_state["chat"].append({
            "role": "assistant",
            "content": "Preciso da chave da Anthropic (barra lateral) para entender pedidos em texto. "
                       "Ou preencha os campos em **Ajustes manuais** abaixo.",
        })
    else:
        current = {
            "niches": [n.strip() for c in st.session_state["niches_raw"].splitlines() for n in c.split(",") if n.strip()],
            "location": st.session_state["location"],
            "area_type": "radius" if st.session_state["mode_label"] == MODE_LABELS[0] else "region",
            "radius_km": st.session_state["radius_km"],
            "site_filter": st.session_state["statuses"],
            "min_rating": st.session_state["min_rating"],
            "min_reviews": st.session_state["min_reviews"],
            "only_phone": st.session_state["only_phone"],
        }
        try:
            res = ai.parse_request(anthropic_key, st.session_state["chat"][:-1], prompt, current)
        except Exception as exc:
            st.session_state["chat"].append({"role": "assistant", "content": f"Não consegui interpretar: {exc}"})
        else:
            if "niches" in res:
                st.session_state["niches_raw"] = "\n".join(res["niches"])
            if "location" in res:
                st.session_state["location"] = res["location"]
            if "area_type" in res:
                st.session_state["mode_label"] = MODE_LABELS[0 if res["area_type"] == "radius" else 1]
            for k in ("radius_km", "statuses", "min_rating", "min_reviews", "only_phone"):
                if k in res:
                    st.session_state[k] = res[k]
            st.session_state["chat"].append({"role": "assistant", "content": res["reply"] or "Ok."})
            ok = (st.session_state["location"].strip() and st.session_state["niches_raw"].strip())
            st.session_state["run_now"] = bool(res["ready"] and ok)

for m in st.session_state["chat"]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# ----------------------------------------------------------- ajustes manuais
with st.expander("⚙️ Ajustes manuais", expanded=not st.session_state["chat"]):
    st.subheader("1. Quem você quer encontrar?")
    niches_raw = st.text_area(
        "Público-alvo (um por linha ou separados por vírgula)", key="niches_raw", height=110,
    )
    niches = [n.strip() for chunk in niches_raw.splitlines() for n in chunk.split(",") if n.strip()]

    st.subheader("2. Onde?")
    mode_label = st.radio("Tipo de área", MODE_LABELS, horizontal=True, key="mode_label")
    mode = lh.MODE_RADIUS if mode_label == MODE_LABELS[0] else lh.MODE_REGION
    col1, col2 = st.columns([3, 2])
    with col1:
        location = st.text_input(
            "Local", key="location",
            placeholder="Ex.: Curitiba, PR  |  Av. Paulista, São Paulo  |  Minas Gerais  |  Portugal",
        )
    with col2:
        radius_km = st.slider("Raio (km)", 1, 300, key="radius_km", disabled=(mode == lh.MODE_REGION))
    if mode == lh.MODE_REGION:
        st.info("Em estado/país a área é varrida em uma grade de círculos de 50 km. "
                "Quanto maior a região, mais requisições (e custo).")

    st.subheader("3. Filtros do resultado")
    f1, f2, f3, f4 = st.columns(4)
    statuses = f1.multiselect("Situação do site", lh.SITE_STATUSES, key="statuses")
    min_rating = f2.slider("Nota mínima", 0.0, 5.0, step=0.5, key="min_rating")
    min_reviews = f3.number_input("Mín. de avaliações", 0, 10000, key="min_reviews")
    only_phone = f4.checkbox("Só com telefone", key="only_phone")

    clicked = st.button("🔎 Buscar clientes", type="primary", disabled=not (location.strip() and niches))

# ----------------------------------------------------------- execução
run = clicked or st.session_state.pop("run_now", False)
if run and location.strip() and niches:
    if not google_key:
        st.error("Informe a chave da Google Places API na barra lateral.")
    else:
        cfg = lh.SearchConfig(
            api_key=google_key, niches=niches, location=location.strip(), mode=mode,
            radius_km=float(radius_km), max_pages=int(max_pages),
            max_requests=int(max_requests), only_operational=only_operational,
        )
        try:
            with st.spinner("Localizando a área..."):
                plan = lh.build_plan(cfg)
            if plan.truncated:
                st.warning(
                    f"A área exige até {plan.est_requests} requisições; só farei {plan.max_requests} "
                    "(limite da barra lateral). Aumente o limite ou reduza a área/nichos para cobrir tudo."
                )
            bar = st.progress(0.0, text=f"Buscando em {len(plan.tiles)} ponto(s)...")

            def on_progress(done, total, _msg):
                bar.progress(done / max(total, 1), text=f"{done}/{total} requisições")

            leads = lh.run_search(cfg, plan, on_progress)
            bar.empty()
            st.session_state["leads"] = leads
            st.session_state["geo"] = plan.geo.name
        except (lh.PlacesError, lh.GeocodeError) as exc:
            st.error(str(exc))
        except Exception as exc:  # rede etc.
            st.error(f"Erro inesperado: {exc}")

# ----------------------------------------------------------- resultado
leads = st.session_state.get("leads")
if leads is not None:
    shown = lh.filter_leads(leads, statuses, min_rating, int(min_reviews), only_phone)
    st.divider()
    st.subheader(f"Resultado — {st.session_state.get('geo', '')}")
    total = len(leads)
    sem = sum(1 for l in leads if l["situacao_site"] == lh.SITE_NONE)
    soc = sum(1 for l in leads if l["situacao_site"] == lh.SITE_SOCIAL)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Encontrados", total)
    m2.metric("Sem site", sem)
    m3.metric("Só rede social/diretório", soc)
    m4.metric("Após filtros", len(shown))

    if shown:
        df = pd.DataFrame(shown)
        cols = ["nome", "nicho", "situacao_site", "telefone", "whatsapp", "site", "nota",
                "avaliacoes", "distancia_km", "endereco", "google_maps"]
        view = df[cols].rename(columns={
            "nome": "Nome", "nicho": "Nicho", "situacao_site": "Site?", "telefone": "Telefone",
            "whatsapp": "WhatsApp", "site": "Site", "nota": "Nota", "avaliacoes": "Aval.",
            "distancia_km": "Dist. (km)", "endereco": "Endereço", "google_maps": "Maps",
        })
        st.dataframe(
            view, width="stretch", hide_index=True,
            column_config={
                "WhatsApp": st.column_config.LinkColumn("WhatsApp", display_text="abrir"),
                "Maps": st.column_config.LinkColumn("Maps", display_text="ver"),
                "Site": st.column_config.LinkColumn("Site"),
            },
        )
        st.map(df.rename(columns={"lat": "latitude", "lon": "longitude"})[["latitude", "longitude"]])

        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Baixar CSV", view.to_csv(index=False).encode("utf-8-sig"),
                           "clientes.csv", "text/csv")
        buf = io.BytesIO()
        view.to_excel(buf, index=False, sheet_name="Clientes")
        d2.download_button(
            "⬇️ Baixar Excel", buf.getvalue(), "clientes.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("Nenhum resultado com esses filtros. Afrouxe os filtros ou aumente a área.")
