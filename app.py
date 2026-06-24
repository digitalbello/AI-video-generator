import streamlit as st
import requests
import asyncio
import edge_tts
from groq import Groq
import json
import os

# API Keys
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
PEXELS_API_KEY = st.secrets["PEXELS_API_KEY"]

RATIOS = {
    "9:16 (TikTok/Shorts)": {"width": 1080, "height": 1920},
    "16:9 (YouTube)": {"width": 1920, "height": 1080},
    "1:1 (Instagram)": {"width": 1080, "height": 1080}
}

def extract_keywords(script):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"Extract 3 visual keywords from: {script}. Return JSON array only."
    completion = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    data = json.loads(completion.choices[0].message.content)
    return list(data.values())[0] if isinstance(data, dict) else data

def search_pexels(query, api_key):
    headers = {"Authorization": api_key}
    url = f"https://api.pexels.com/videos/search?query={query}&per_page=3"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        videos = resp.json().get("videos", [])
        if videos:
            return videos[0]["video_files"][-1]["link"]
    except:
        pass
    return None

async def generate_audio(text, output):
    comm = edge_tts.Communicate(text, "en-US-ChristopherNeural")
    await comm.save(output)

st.title("🎬 AI Video Creator")
st.write("⚠️ Note: This creates audio + video links. Download separately and edit in CapCut/InShot")

script = st.text_area("📝 Script:", height=150)
ratio = st.selectbox("📐 Ratio:", list(RATIOS.keys()))

if st.button("🚀 Generate", type="primary"):
    if script:
        with st.spinner("Thinking..."):
            keywords = extract_keywords(script)
            st.success(f"Keywords: {', '.join(keywords)}")
        
        with st.spinner("Voice..."):
            asyncio.run(generate_audio(script, "audio.mp3"))
            st.audio("audio.mp3")
            with open("audio.mp3", "rb") as f:
                st.download_button("⬇️ Download Audio", f, "audio.mp3")
        
        with st.spinner("Finding videos..."):
            video_links = []
            for kw in keywords:
                url = search_pexels(kw, PEXELS_API_KEY)
                if url:
                    video_links.append((kw, url))
                    st.video(url)
                    st.markdown(f"**{kw}**: [Download]({url})")
        
        st.info("💡 Tip: Download audio + videos, then combine in CapCut (free mobile app)")
    else:
        st.error("Enter a script!")
