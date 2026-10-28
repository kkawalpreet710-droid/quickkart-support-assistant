import os
import streamlit as st
import chromadb
from pypdf import PdfReader
from groq import Groq
from langchain_text_splitters import RecursiveCharacterTextSplitter

from agent import open_database, run_agent

MODEL = "openai/gpt-oss-20b"
POLICY_FOLDER = "policies"
MAX_QUESTIONS_PER_SESSION = 10
MAX_AGENT_QUESTIONS_PER_SESSION = 5

st.set_page_config(page_title="QuickKart Support Assistant")


@st.cache_resource
def build_collection():
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)
    chunks = []
    for name in sorted(os.listdir(POLICY_FOLDER)):
        path = os.path.join(POLICY_FOLDER, name)
        if name.endswith(".txt"):
            with open(path, encoding="utf-8") as f:
                text = f.read()
        elif name.endswith(".pdf"):
            text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
        else:
            continue
        for piece in splitter.split_text(text):
            chunks.append({"text": piece, "source": name})

    client = chromadb.Client()
    collection = client.get_or_create_collection("quickkart")
    collection.add(
        ids=[f"chunk{i}" for i in range(len(chunks))],
        documents=[c["text"] for c in chunks],
        metadatas=[{"source": c["source"]} for c in chunks],
    )
    return collection


@st.cache_resource
def get_connection():
    return open_database()


@st.cache_resource
def answer_cache():
    return {}


collection = build_collection()
groq_client = Groq(api_key=st.secrets["GROQ_API_KEY"])


def retrieve(question, k=5):
    res = collection.query(query_texts=[question], n_results=k)
    return list(zip(res["metadatas"][0], res["documents"][0]))


def ask_model(question, passages):
    context = ""
    for meta, text in passages:
        context += f"[Source: {meta['source']}]\n{text}\n\n"

    prompt = f"""You are a customer support assistant for QuickKart.
Answer the question using ONLY the policy excerpts below.
- Mention the source file name(s) you used.
- If the excerpts do not contain the answer, reply exactly: "I don't know based on the policy documents."
- Do not guess or use outside knowledge.
- If a rule depends on a time, amount or condition, compare it with the details in the question and say which case applies, with the number for that case.
- If the answer depends on a condition the question does not mention, state that condition.

POLICY EXCERPTS:
{context}
QUESTION: {question}"""

    response = groq_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return response.choices[0].message.content


st.title("QuickKart Support Assistant")
st.caption(
    "A demo of retrieval-augmented generation (RAG) and a tool-using agent. "
    "QuickKart is a fictional company. Policy answers come only from 7 documents "
    "written for this project, and orders come from a synthetic database."
)

mode = st.radio(
    "Mode",
    ["Policy assistant", "Agent (policy + sample orders)"],
    horizontal=True,
)
agent_mode = mode.startswith("Agent")

if agent_mode:
    st.info(
        "The agent can look up 40 sample orders (ids 1001 to 1040) and calculate credits "
        "and refunds. It makes several API calls per question, so each visit is limited "
        f"to {MAX_AGENT_QUESTIONS_PER_SESSION} questions."
    )
    examples = [
        "My order 1001 came late and an item was missing. What can I claim?",
        "Order 1004 had an item missing. What refund do I get, and are the delivery fees refunded too?",
        "What happened with order 9999?",
        "How long do I have to report a problem with fresh milk compared with packaged noodles?",
    ]
else:
    examples = [
        "One item was missing from my order. How much refund do I get?",
        "I cancelled my order 10 minutes after placing it. What will I pay?",
        "I received a yogurt that was past its expiry date. What do I get?",
        "Does QuickKart sell alcohol or cigarettes?",
    ]

with st.expander("Example questions to try"):
    for e in examples:
        st.write(f"- {e}")

if "count" not in st.session_state:
    st.session_state.count = 0
if "agent_count" not in st.session_state:
    st.session_state.agent_count = 0

question = st.text_input("Ask a question")

if st.button("Ask") and question.strip():
    question = question.strip()
    cache = answer_cache()
    cache_key = ("agent" if agent_mode else "policy", question)
    reply, passages, trace = None, [], []

    if cache_key in cache:
        reply, passages, trace = cache[cache_key]
    else:
        used = st.session_state.agent_count if agent_mode else st.session_state.count
        limit = MAX_AGENT_QUESTIONS_PER_SESSION if agent_mode else MAX_QUESTIONS_PER_SESSION
        if used >= limit:
            st.warning(f"This demo allows {limit} new questions per visit in this mode. Please come back later.")
        else:
            try:
                with st.spinner("Thinking..."):
                    if agent_mode:
                        reply, trace = run_agent(
                            question, groq_client, MODEL, get_connection(), collection
                        )
                    else:
                        passages = retrieve(question)
                        reply = ask_model(question, passages)
                if not reply.startswith("Stopped"):
                    cache[cache_key] = (reply, passages, trace)
                if agent_mode:
                    st.session_state.agent_count += 1
                else:
                    st.session_state.count += 1
            except Exception as e:
                reply = None
                if "429" in str(e):
                    st.error("The demo's free API limit has been reached for now. Please try again later.")
                else:
                    st.error("Something went wrong. Please try again.")

    if reply:
        st.markdown(reply)
        if trace:
            with st.expander("Tools the agent used"):
                for step in trace:
                    st.code(step)
        if passages:
            with st.expander("Passages the model was shown"):
                for meta, text in passages:
                    st.markdown(f"**{meta['source']}**")
                    st.text(text)
