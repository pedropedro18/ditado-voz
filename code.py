import base64
import difflib
import hmac
import html
import io
import json
import re
import time
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components
from gtts import gTTS

try:
    from google import genai
except ImportError:
    genai = None

# ----------------------------- Configuração -----------------------------
MODELO_PADRAO = "gemini-3.5-flash-lite"
MODELOS_RESERVA = ["gemini-3.5-flash", "gemini-2.0-flash"]

NUMERO_WHATSAPP = "923030010"  # para enviar o comprovativo
NUMERO_EXPRESS = "923030010"  # número para o pagamento Express
PRECO = "3.000 Kz"
INDICATIVO = "244"  # Angola

# nome -> (código gTTS, sotaque, nome para a IA)
IDIOMAS = {
    "Português (PT)": ("pt", "pt", "Português de Portugal"),
    "Português (BR)": ("pt", "com.br", "Português do Brasil"),
    "Français": ("fr", "fr", "Français"),
    "English (US)": ("en", "com", "English"),
    "English (UK)": ("en", "co.uk", "English"),
}
LINGUAS = ["Português", "Français", "English"]

PONTUACAO = {
    "pt": {",": " vírgula ", ".": " ponto final ", "?": " ponto de interrogação ",
           "!": " ponto de exclamação ", ":": " dois pontos ", ";": " ponto e vírgula "},
    "en": {",": " comma ", ".": " full stop ", "?": " question mark ",
           "!": " exclamation mark ", ":": " colon ", ";": " semicolon "},
    "fr": {",": " virgule ", ".": " point ", "?": " point d'interrogation ",
           "!": " point d'exclamation ", ":": " deux points ", ";": " point-virgule "},
}

MAX_FRASES = 25
MAX_CARACTERES = 3000

st.set_page_config(page_title="Ditado automático", page_icon="📝")
st.title("📝 Ditado automático com voz")
st.caption(
    "Ditado em português, francês e inglês, com correção, explicação, "
    "tradução e perguntas"
)

for chave, valor in [("acesso", False), ("pedir_pagamento", False),
                     ("ditado", None), ("correcao", None), ("explicacao", None)]:
    if chave not in st.session_state:
        st.session_state[chave] = valor


# ------------------------------- Acesso ---------------------------------
def codigos_validos():
    try:
        return [str(c).strip().upper() for c in st.secrets["CODIGOS_ACESSO"]]
    except Exception:
        return []


def codigo_correto(digitado):
    digitado = digitado.strip().upper()
    return any(hmac.compare_digest(digitado, c) for c in codigos_validos())


def painel_pagamento():
    st.warning("🔒 Para usar o ditado precisa de uma palavra-passe (código de acesso).")
    st.markdown(
        f"""
*Como obter a palavra-passe:*
1. Faça o pagamento de *{PRECO}* por *Express* para *{NUMERO_EXPRESS}*.
2. Envie o *comprovativo* pelo WhatsApp.
3. Receberá a sua *palavra-passe*. Escreva-a abaixo.
"""
    )
    msg = quote(
        f"Olá! Fiz o pagamento de {PRECO} por Express para o Ditado automático. "
        "Segue o comprovativo. Podem enviar-me a palavra-passe?"
    )
    st.link_button("📲 Enviar comprovativo no WhatsApp",
                   f"https://wa.me/{INDICATIVO}{NUMERO_WHATSAPP}?text={msg}")
    codigo = st.text_input("Palavra-passe", type="password", key="campo_codigo")
    if st.button("Validar palavra-passe"):
        if codigo_correto(codigo):
            st.session_state.acesso = True
            st.session_state.pedir_pagamento = False
            st.rerun()
        else:
            st.error("Palavra-passe inválida. Confira e tente novamente.")


def exigir_acesso():
    if st.session_state.acesso:
        return True
    st.session_state.pedir_pagamento = True
    return False


# ------------------------------ Áudio / texto ---------------------------
def dividir_frases(texto):
    partes = re.split(r"(?<=[.!?。！？])\s+|\n+", texto.strip())
    return [p.strip() for p in partes if p.strip()]


def com_pontuacao(frase, lang):
    mapa = PONTUACAO.get(lang)
    if not mapa:
        return frase
    for sinal, nome in mapa.items():
        frase = frase.replace(sinal, nome)
    return re.sub(r"\s+", " ", frase).strip()


@st.cache_data(show_spinner=False)
def audio_bytes(texto, lang, tld, lento):
    buf = io.BytesIO()
    gTTS(texto, lang=lang, tld=tld, slow=lento).write_to_fp(buf)
    return buf.getvalue()


def audio_base64(texto, lang, tld, lento):
    return base64.b64encode(audio_bytes(texto, lang, tld, lento)).decode()


def tokens(texto, estrito):
    padrao = r"\w+|[^\w\s]" if estrito else r"\w+"
    return re.findall(padrao, texto)


def corrigir(original, escrito, estrito):
    """Compara palavra a palavra e devolve (html, certas, total)."""
    orig = tokens(original, estrito)
    resp = tokens(escrito, estrito)
    if estrito:
        a, b = orig, resp
    else:
        a, b = [t.lower() for t in orig], [t.lower() for t in resp]

    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    partes, certas = [], 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            certas += i2 - i1
            partes += [f"<span style='color:#2E7D32'>{html.escape(t)}</span>" for t in orig[i1:i2]]
        else:
            for t in resp[j1:j2]:
                partes.append(
                    f"<span style='color:#C62828;text-decoration:line-through'>{html.escape(t)}</span>")
            for t in orig[i1:i2]:
                partes.append(
                    f"<span style='color:#E65100;font-weight:bold;text-decoration:underline'>{html.escape(t)}</span>")
    return " ".join(partes), certas, len(orig)


# ------------------------------- Gemini ---------------------------------
def obter_cliente():
    if genai is None:
        return None
    try:
        return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    except Exception:
        return None


def lista_de_modelos():
    try:
        principal = str(st.secrets["MODELO"]).strip()
    except Exception:
        principal = MODELO_PADRAO
    return [principal] + [m for m in MODELOS_RESERVA if m != principal]


@st.cache_data(show_spinner=False, ttl=3600)
def _gemini(prompt):
    """Só guarda em cache as respostas certas (as falhas levantam erro)."""
    cliente = obter_cliente()
    if cliente is None:
        raise RuntimeError("Chave GEMINI_API_KEY não configurada nos Secrets.")
    ultimo = ""
    for modelo in lista_de_modelos():
        espera = 2
        for tentativa in range(3):
            try:
                resp = cliente.models.generate_content(model=modelo, contents=prompt)
                if resp.text:
                    return resp.text
                raise RuntimeError("Sem resposta.")
            except RuntimeError:
                raise
            except Exception as e:
                msg = str(e)
                ultimo = msg
                if "429" in msg or "quota" in msg.lower():
                    if tentativa < 2:
                        time.sleep(espera)
                        espera *= 2
                        continue
                    raise RuntimeError("Limite de uso da API atingido. Tente daqui a alguns minutos.")
                if "404" in msg or "NOT_FOUND" in msg:
                    break
                raise RuntimeError(f"Erro da API: {msg[:200]}")
    raise RuntimeError(f"Nenhum modelo disponível. Último erro: {ultimo[:200]}")


def perguntar_ia(prompt):
    try:
        return _gemini(prompt), True
    except Exception as e:
        return f"⚠️ {e}", False


# ------------------------------ Player JS -------------------------------
PLAYER = """
<div style="font-family:Arial,sans-serif;padding:12px;border:1px solid #ccc;border-radius:10px;background:#FFF8E1">
  <button id="start" style="font-size:16px;padding:8px 16px;border-radius:8px;border:0;background:#2E7D32;color:#fff;cursor:pointer">▶️ Iniciar ditado</button>
  <button id="stop" style="font-size:16px;padding:8px 16px;border-radius:8px;border:0;background:#C62828;color:#fff;cursor:pointer;margin-left:6px">⏹️ Parar</button>
  <div style="margin:12px 0 6px;height:10px;background:#e0e0e0;border-radius:6px;overflow:hidden">
    <div id="bar" style="height:10px;width:0;background:#F9A825"></div>
  </div>
  <div id="status" style="font-size:17px;color:#333">Pronto. Carregue em "Iniciar ditado".</div>
</div>
<script>
const ITEMS = _ITEMS_;
const REPS = _REPS_;
const GAP = _GAP_;
let stopped = false, running = false, audio = null;
const statusEl = document.getElementById("status");
const bar = document.getElementById("bar");
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function play(b64) {
  return new Promise(res => {
    audio = new Audio("data:audio/mpeg;base64," + b64);
    audio.onended = res; audio.onerror = res;
    audio.play().catch(res);
  });
}
async function countdown(ms, label) {
  let left = Math.ceil(ms / 1000);
  while (left > 0 && !stopped) {
    statusEl.textContent = label + " " + left + "s";
    await sleep(1000); left--;
  }
}
async function run() {
  if (running) return;
  running = true; stopped = false;
  for (let i = 0; i < ITEMS.length && !stopped; i++) {
    bar.style.width = (i / ITEMS.length * 100) + "%";
    for (let r = 0; r < REPS && !stopped; r++) {
      statusEl.textContent = "🔊 Frase " + (i + 1) + " de " + ITEMS.length + " - ouça (" + (r + 1) + "/" + REPS + ")";
      await play(ITEMS[i].b64);
      if (r < REPS - 1 && !stopped) await sleep(GAP);
    }
    if (stopped) break;
    await countdown(ITEMS[i].wait, "✍️ Frase " + (i + 1) + " - escreva. Tempo:");
  }
  if (stopped) { statusEl.textContent = "⏹️ Ditado parado."; }
  else { bar.style.width = "100%"; statusEl.textContent = "✅ Fim do ditado! Agora escreva/confira abaixo."; }
  running = false;
}
function stop() { stopped = true; if (audio) audio.pause(); }
document.getElementById("start").onclick = run;
document.getElementById("stop").onclick = stop;
</script>
"""

# ------------------------------ Interface -------------------------------
idioma = st.selectbox("Idioma do texto", list(IDIOMAS.keys()))
lang, tld, nome_idioma = IDIOMAS[idioma]

texto = st.text_area("Texto do ditado", height=180,
                     placeholder="Cole aqui o texto que o aluno vai escrever...")

aba_dit, aba_trad, aba_perg, aba_priv = st.tabs(
    ["📝 Ditado", "🌍 Tradução", "❓ Perguntas", "🔐 Privacidade"]
)

# ============================== DITADO ===================================
with aba_dit:
    c1, c2, c3 = st.columns(3)
    reps = c1.slider("Repetições por frase", 1, 3, 2)
    pausa = c2.slider("Pausa entre repetições (s)", 1, 5, 2)
    seg_palavra = c3.slider("Tempo para escrever (s/palavra)", 0.5, 3.0, 1.2, 0.1)
    o1, o2 = st.columns(2)
    lento = o1.checkbox("Voz lenta (bom para iniciantes)", value=True)
    ditar_pont = o2.checkbox("Ditar a pontuação (vírgula, ponto...)", value=False)

    if st.button("🎧 Preparar ditado", type="primary"):
        frases = dividir_frases(texto)
        if not frases:
            st.warning("Escreva ou cole um texto primeiro.")
        elif len(frases) > MAX_FRASES or len(texto) > MAX_CARACTERES:
            st.warning(f"Texto demasiado longo. Máximo: {MAX_FRASES} frases e {MAX_CARACTERES} caracteres.")
        elif exigir_acesso():
            try:
                with st.spinner("A preparar o áudio..."):
                    itens = []
                    for f in frases:
                        falado = com_pontuacao(f, lang) if ditar_pont else f
                        n_pal = max(1, len(tokens(f, False)))
                        itens.append({
                            "b64": audio_base64(falado, lang, tld, lento),
                            "wait": int(max(4, n_pal * seg_palavra) * 1000),
                        })
                st.session_state.ditado = {
                    "itens": itens, "reps": reps, "gap": int(pausa * 1000),
                    "original": " ".join(frases), "n_frases": len(frases),
                    "idioma": idioma,
                }
                st.session_state.correcao = None
                st.session_state.explicacao = None
            except Exception as e:
                st.error(f"Não foi possível gerar o áudio: {e}")

    d = st.session_state.ditado
    if d:
        st.success(f"Ditado pronto: {d['n_frases']} frases. Carregue em Iniciar.")
        pagina = (PLAYER.replace("_ITEMS_", json.dumps(d["itens"]))
                  .replace("_REPS_", str(d["reps"]))
                  .replace("_GAP_", str(d["gap"])))
        components.html(pagina, height=170)

        st.subheader("✍️ O que o aluno escreveu")
        with st.form("form_correcao"):
            escrito = st.text_area("Escreva aqui o que ouviu", height=180)
            estrito = st.checkbox("Contar maiúsculas e pontuação como erro", value=False)
            enviar = st.form_submit_button("✅ Corrigir")

        if enviar:
            if not escrito.strip():
                st.warning("Escreva o texto primeiro.")
            else:
                corpo, certas, total = corrigir(d["original"], escrito, estrito)
                st.session_state.correcao = {
                    "html": corpo, "certas": certas, "total": total,
                    "nota": round(certas / total * 10, 1) if total else 0,
                    "escrito": escrito,
                }
                st.session_state.explicacao = None

        cor = st.session_state.correcao
        if cor:
            st.metric("Nota", f"{cor['nota']} / 10", f"{cor['certas']} de {cor['total']} certas")
            st.markdown("<div style='font-size:18px;line-height:1.9'>" + cor["html"] + "</div>",
                        unsafe_allow_html=True)
            st.caption("🟢 certo   🔴 riscado = escrito errado ou a mais   "
                       "🟠 sublinhado = palavra certa que faltou ou foi mal escrita")
            with st.expander("Ver o texto original"):
                st.write(d["original"])

            st.divider()
            st.subheader("🤖 Explicação dos erros")
            expl_em = st.selectbox("Explicar em", LINGUAS, key="expl_em")
            if cor["certas"] == cor["total"]:
                st.success("Sem erros. Parabéns! 🎉")
            elif st.button("Explicar os erros"):
                if exigir_acesso():
                    prompt = (
                        f"És um professor de {nome_idioma} para alunos da 4ª classe. "
                        "Compara o texto ORIGINAL do ditado com o texto ESCRITO pelo aluno. "
                        "Para cada erro indica: o que o aluno escreveu, a forma correta e uma "
                        "explicação curta e simples da regra (ortografia, acentuação, "
                        f"concordância, etc.). Responde em {expl_em}. "
                        "No fim, dá 2 dicas simples para melhorar.\n\n"
                        f"ORIGINAL:\n{d['original']}\n\nESCRITO PELO ALUNO:\n{cor['escrito']}"
                    )
                    with st.spinner("A analisar os erros..."):
                        resp, ok = perguntar_ia(prompt)
                    st.session_state.explicacao = resp
            if st.session_state.explicacao:
                st.markdown(st.session_state.explicacao)

        if st.button("🗑️ Novo ditado"):
            st.session_state.ditado = None
            st.session_state.correcao = None
            st.session_state.explicacao = None
            st.rerun()

# ============================== TRADUÇÃO =================================
with aba_trad:
    destino = st.selectbox("Traduzir para", LINGUAS, key="destino")
    if st.button("Traduzir"):
        if not texto.strip():
            st.warning("Escreva ou cole o texto primeiro.")
        elif exigir_acesso():
            prompt = (f"Traduz o seguinte texto de {nome_idioma} para {destino}. "
                      f"Devolve apenas a tradução.\n\nTexto:\n{texto}")
            with st.spinner("A traduzir..."):
                resp, ok = perguntar_ia(prompt)
            st.markdown(resp)
            if ok:
                cod = {"Português": ("pt", "pt"), "Français": ("fr", "fr"),
                       "English": ("en", "com")}[destino]
                try:
                    st.caption("🔊 Ouvir a tradução")
                    st.audio(audio_bytes(resp[:3000], cod[0], cod[1], False), format="audio/mp3")
                except Exception as e:
                    st.error(f"Erro no áudio: {e}")

# ============================== PERGUNTAS ================================
with aba_perg:
    modo = st.radio("O que deseja?",
                    ["Gerar perguntas sobre o texto", "Fazer uma pergunta"], horizontal=True)
    pergunta = ""
    if modo == "Fazer uma pergunta":
        pergunta = st.text_input("A sua pergunta sobre o texto")
    if st.button("Executar", key="exec_perg"):
        if not texto.strip():
            st.warning("Escreva ou cole o texto primeiro.")
        elif modo == "Fazer uma pergunta" and not pergunta.strip():
            st.warning("Escreva a sua pergunta.")
        elif exigir_acesso():
            if modo == "Gerar perguntas sobre o texto":
                prompt = (
                    f"Cria 5 perguntas de compreensão em {nome_idioma}, adequadas a alunos "
                    "da 4ª classe, sobre o texto abaixo. Numera-as. Depois, numa secção "
                    f"'Respostas', dá a resposta de cada uma.\n\nTexto:\n{texto}")
            else:
                prompt = (
                    "Com base no texto abaixo, responde à pergunta na mesma língua da "
                    f"pergunta.\n\nTexto:\n{texto}\n\nPergunta: {pergunta}")
            with st.spinner("A pensar..."):
                resp, ok = perguntar_ia(prompt)
            st.markdown(resp)

# ============================== PRIVACIDADE ==============================
with aba_priv:
    st.subheader("Política de privacidade")
    st.markdown(
        """
*Que dados usamos*
- O texto que escreve ou cola e o que o aluno escreve, apenas para fazer o ditado, a correção, a explicação, a tradução e as perguntas.
- A palavra-passe, apenas para validar a sua entrada.

*O que fazemos com eles*
- *Não guardamos* os seus textos em nenhuma base de dados nem os usamos para outros fins.
- *Não vendemos, não partilhamos e não cedemos* os seus dados a terceiros para publicidade ou qualquer outro fim.
- O comprovativo de pagamento enviado pelo WhatsApp serve só para confirmar o pagamento e enviar a palavra-passe.

*Serviços técnicos necessários*
Para funcionar, o texto é enviado de forma temporária aos serviços da Google que
geram a voz (síntese de voz) e as respostas de IA (Gemini). Esses serviços
processam o texto apenas para devolver o resultado, e têm as suas próprias
políticas de privacidade.

*Os seus direitos*
Pode pedir esclarecimentos ou a eliminação do comprovativo enviado, contactando-nos pelo WhatsApp.
"""
    )

if st.session_state.pedir_pagamento and not st.session_state.acesso:
    st.divider()
    painel_pagamento()