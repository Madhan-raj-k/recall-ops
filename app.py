"""RecallOps Streamlit UI.  Run:  streamlit run app.py"""
from __future__ import annotations

import streamlit as st

from agent import record_resolution, triage
from config import load_settings
from incidents_data import DEMO_ALERTS, seed_incidents
from llm import build_llm
from memory import HindsightIncidentMemory, InMemoryIncidentMemory, MemoryError_

st.set_page_config(page_title="RecallOps", page_icon="🧠", layout="wide")
settings = load_settings()


def get_memory():
    """Hindsight when configured; otherwise a per-session offline store seeded with demo data.

    A fresh Hindsight client per call keeps the sync client's event loop on the current thread.
    """
    if settings.hindsight_ready:
        return HindsightIncidentMemory(settings.hindsight_base_url, settings.hindsight_api_key, settings.bank_id)
    if "offline_memory" not in st.session_state:
        mem = InMemoryIncidentMemory()
        mem.retain_incidents(seed_incidents())
        st.session_state.offline_memory = mem
    return st.session_state.offline_memory


llm = build_llm(settings)
memory = get_memory()

# ---------------- sidebar ----------------
with st.sidebar:
    st.title("🧠 RecallOps")
    st.caption("On-call copilot that remembers every incident.")
    st.markdown(f"**Memory:** {'🟢 Hindsight · `' + settings.bank_id + '`' if settings.hindsight_ready else '🟡 offline demo store'}")
    st.markdown(f"**LLM:** {'🟢 Groq · `' + settings.groq_model + '`' if llm else '🟡 not configured (rule-based plan)'}")
    include_reflection = st.checkbox("Also run Hindsight reflect", value=True, help="Adds a memory-grounded summary; slower.")
    if settings.hindsight_ready and st.button("Seed synthetic incident history"):
        with st.spinner("Retaining 18 synthetic incidents into Hindsight..."):
            try:
                n = memory.retain_incidents(seed_incidents())
                st.success(f"Retained {n} incidents.")
            except MemoryError_ as exc:
                st.error(str(exc))
    st.caption("Demo data is synthetic (fictional company ShopNova).")


def show_result(res, header: str) -> None:
    st.subheader(header)
    for w in res.warnings:
        st.warning(w)
    st.markdown(res.plan)
    if res.use_memory:
        with st.expander(f"🔎 {len(res.memories)} memories recalled from Hindsight", expanded=False):
            for m in res.memories:
                meta = " · ".join(x for x in [m.type, (m.occurred or "")[:10], m.document_id or ""] if x)
                st.markdown(f"- {m.text}  \n  <small>{meta}</small>", unsafe_allow_html=True)
        if res.reflection:
            with st.expander("🪞 Hindsight reflect"):
                st.markdown(res.reflection)


tab_triage, tab_resolve, tab_explore = st.tabs(["🚨 Triage incident", "✅ Record resolution", "🗂️ Memory explorer"])

with tab_triage:
    st.markdown("Paste an alert. Compare a copilot **without memory** to one that **recalls past incidents**.")
    preset = st.selectbox("Example alerts", ["(type your own)"] + DEMO_ALERTS)
    alert = st.text_area("Alert / incident description", value="" if preset == "(type your own)" else preset, height=100)
    mode = st.radio("Mode", ["Side-by-side (OFF vs ON)", "Memory ON", "Memory OFF"], horizontal=True)
    if st.button("Triage", type="primary", disabled=not alert.strip()):
        runs = {"Side-by-side (OFF vs ON)": [False, True], "Memory ON": [True], "Memory OFF": [False]}[mode]
        cols = st.columns(len(runs))
        for col, use_mem in zip(cols, runs):
            with col, st.spinner("Recalling and planning..." if use_mem else "Planning without memory..."):
                res = triage(alert, memory, llm, use_memory=use_mem, include_reflection=include_reflection)
                if use_mem:
                    st.session_state.last_recall = res
                show_result(res, "🧠 Memory ON" if use_mem else "🫥 Memory OFF")

with tab_resolve:
    st.markdown("After the incident, record what really happened. It is **retained** into Hindsight, so the next similar alert gets a better plan.")
    with st.form("resolution", clear_on_submit=True):
        c1, c2, c3 = st.columns([3, 2, 1])
        title = c1.text_input("Title *", placeholder="Payments API 5xx after deploy v3.21.0")
        service = c2.text_input("Service *", placeholder="payments-api")
        severity = c3.selectbox("Severity", ["SEV1", "SEV2", "SEV3", "SEV4"], index=1)
        symptoms = st.text_area("Symptoms / alerts", height=70)
        root_cause = st.text_area("Root cause *", height=70)
        what_worked = st.text_area("What fixed it *", height=70)
        what_failed = st.text_area("What did NOT work", height=70)
        c4, c5, c6 = st.columns(3)
        teams = c4.text_input("Teams (comma separated)", placeholder="payments, sre")
        runbook = c5.text_input("Runbook")
        ttr = c6.number_input("Minutes to resolve", min_value=0, value=30, step=5)
        submitted = st.form_submit_button("Retain postmortem", type="primary")
    if submitted:
        try:
            inc = record_resolution(
                memory, title=title, service=service, severity=severity, symptoms=symptoms,
                root_cause=root_cause, what_worked=what_worked, what_failed=what_failed,
                teams=teams.split(","), runbook=runbook, time_to_resolve_min=int(ttr),
            )
            st.success(f"Retained {inc.incident_id}. Try triaging a similar alert now.")
            with st.expander("What was sent to Hindsight retain()"):
                st.code(inc.to_memory_text())
                st.json({"tags": inc.tags(), "metadata": inc.metadata(), "timestamp": inc.occurred_at.isoformat()})
        except ValueError as exc:
            st.error(str(exc))
        except MemoryError_ as exc:
            st.error(f"Could not retain: {exc}")

with tab_explore:
    st.markdown("See exactly what the agent remembers and what it recalled.")
    last = st.session_state.get("last_recall")
    if last:
        st.markdown(f"**Last recall** for: _{last.alert}_ — {len(last.memories)} memories")
        st.dataframe(
            [{"type": m.type, "when": (m.occurred or "")[:10], "source": m.document_id, "score": m.score, "memory": m.text} for m in last.memories],
            width="stretch",
        )
    q = st.text_input("Ad-hoc recall query", placeholder="What fixed Redis eviction before?")
    c1, c2 = st.columns(2)
    if c1.button("Recall", disabled=not q.strip()):
        try:
            hits = memory.recall_similar(q)
            st.dataframe([{"type": m.type, "when": (m.occurred or "")[:10], "source": m.document_id, "memory": m.text} for m in hits], width="stretch")
        except MemoryError_ as exc:
            st.error(str(exc))
    if c2.button("Reflect", disabled=not q.strip()):
        try:
            st.markdown(memory.reflect(q) or "_(empty)_")
        except MemoryError_ as exc:
            st.error(str(exc))
    if st.button("List stored memories"):
        try:
            rows = memory.list_recent(limit=100)
            st.caption(f"{len(rows)} memory units")
            st.dataframe([{"type": m.type, "when": (m.occurred or "")[:10], "source": m.document_id, "memory": m.text} for m in rows], width="stretch")
        except MemoryError_ as exc:
            st.error(str(exc))

# Release the Hindsight HTTP session created for this rerun.
memory.close()
