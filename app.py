import os
import asyncio
import requests
import tempfile
import streamlit as st
import edge_tts
from groq import Groq
from pexels_api import API
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips
import random
import json

# Get API keys from Streamlit Cloud Secrets
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
PEXELS_API_KEY = st.secrets["PEXELS_API_KEY"]

RATIOS = {
    "9:16 (TikTok/Shorts)": (1080, 1920),
    "16:9 (YouTube)": (1920, 1080),
    "1:1 (Instagram)": (1080, 1080)
}

def extract_keywords(script):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"Extract exactly 3 simple visual search keywords from this script. Return ONLY a JSON array of strings. Script: {script}"
    completion = client.chat.completions.create(model="llama3-8b-8192", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
    data = json.loads(completion.choices[0].message.content)
    return list(data.values())[0] if isinstance(data, dict) else data

def generate_audio_sync(text, output_path):
    # Safe async wrapper for cloud servers
    async def _gen():
        communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
        await communicate.save(output_path)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_gen())

def get_stock_videos(keywords):
    api = API(PEXELS_API_KEY)
    urls = []
    for kw in keywords:
        api.search(kw, media_type="video")
        entries = api.get_entries()
        if entries:
            urls.append(random.choice(entries).video)
    return urls

def crop_and_resize(clip, w, h):
    scale = max(w / clip.w, h / clip.h)
    clip = clip.resize(scale)
    x1, y1 = (clip.w - w) / 2, (clip.h - h) / 2
    return clip.crop(x1=x1, y1=y1, width=w, height=h)

# --- UI ---
st.set_page_config(page_title="AI Video Gen", layout="wide")
st.title("🎬 AI Video Generator")

script = st.text_area("📝 Paste your script:", height=150)
ratio = st.selectbox("📐 Select Ratio:", list(RATIOS.keys()))

if st.button("🚀 Generate Video", type="primary", use_container_width=True):
    if not script.strip():
        st.error("Please enter a script!")
    else:
        with st.spinner("🧠 AI is analyzing script..."):
            keywords = extract_keywords(script)
            st.write(f"**Visuals:** {', '.join(keywords)}")
            
        with st.spinner("🎙️ Generating voice..."):
            audio_path = "voice.mp3"
            generate_audio_sync(script, audio_path)
            
        with st.spinner("🎬 Fetching stock footage..."):
            vid_urls = get_stock_videos(keywords)
            
        with st.spinner("✂️ Editing video..."):
            tw, th = RATIOS[ratio]
            audio_clip = AudioFileClip(audio_path)
            time_per = audio_clip.duration / len(vid_urls)
            
            clips = []
            for url in vid_urls:
                temp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
                with open(temp, 'wb') as f: f.write(requests.get(url).content)
                
                v_clip = VideoFileClip(temp).subclip(0, time_per)
                clips.append(crop_and_resize(v_clip, tw, th))
                
            final = concatenate_videoclips(clips, method="compose").set_audio(audio_clip)
            final.write_videofile("output.mp4", codec="libx264", audio_codec="aac", fps=30, logger=None)
            
        st.success("✅ Done!")
        st.video("output.mp4")
        
        with open("output.mp4", "rb") as f:
            st.download_button("⬇️ Download Video", f, file_name="video.mp4", mime="video/mp4")
