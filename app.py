import os
import streamlit as st
import chromadb
from pypdf import PdfReader
from groq import Groq
from langchain_text_splitters import RecursiveCharacterTextSplitter

MODEL = "openai/gpt-oss-20b"
POLICY_FOLDER = "policies"
MAX_QUESTIONS_PER_SESSION = 10

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
    "A demo of retrieval-augmented generation (RAG). QuickKart is a fictional company. "
    "Answers come only from 7 policy documents written for this project."
)

with st.expander("Example questions to try"):
    st.write("- One item was missing from my order. How much refund do I get?")
    st.write("- I cancelled my order 10 minutes after placing it. What will I pay?")
    st.write("- I received a yogurt that was past its expiry date. What do I get?")
    st.write("- Does QuickKart sell alcohol or cigarettes?")

if "count" not in st.session_state:
    st.session_state.count = 0

question = st.text_input("Ask a question about QuickKart's policies")

if st.button("Ask") and question.strip():
    question = question.strip()
    cache = answer_cache()
    reply = None
    passages = retrieve(question)

    if question in cache:
        reply = cache[question]
    elif st.session_state.count >= MAX_QUESTIONS_PER_SESSION:
        st.warning("This demo allows 10 new questions per visit. Please come back later.")
    else:
        try:
            with st.spinner("Thinking..."):
                reply = ask_model(question, passages)
            cache[question] = reply
            st.session_state.count += 1
        except Exception as e:
            if "429" in str(e):
                st.error("The demo's free API limit has been reached for now. Please try again later.")
            else:
                st.error("Something went wrong. Please try again.")

    if reply:
        st.markdown(reply)
        with st.expander("Passages the model was shown"):
            for meta, text in passages:
                st.markdown(f"**{meta['source']}**")
                st.text(text)
