import os
import asyncio
import requests
import tempfile
import streamlit as st
import edge_tts
from groq import Groq
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips
import random
import json

# Get API keys
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
    completion = client.chat.completions.create(
        model="llama3-8b-8192", 
        messages=[{"role": "user", "content": prompt}], 
        response_format={"type": "json_object"}
    )
    data = json.loads(completion.choices[0].message.content)
    return list(data.values())[0] if isinstance(data, dict) else data

def generate_audio_sync(text, output_path):
    async def _gen():
        communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
        await communicate.save(output_path)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_gen())

def search_pexels_videos(query, api_key):
    """Direct API call to Pexels instead of using pexels_py"""
    headers = {"Authorization": api_key}
    url = f"https://api.pexels.com/videos/search?query={query}&per_page=5&orientation=portrait"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if data.get("videos"):
            # Get a random video from results            video = random.choice(data["videos"])
            # Get the highest quality video file
            video_files = video.get("video_files", [])
            if video_files:
                return video_files[-1]["link"]  # Last is usually highest quality
    except:
        pass
    return None

def crop_and_resize(clip, w, h):
    scale = max(w / clip.w, h / clip.h)
    clip = clip.resize(scale)
    x1, y1 = (clip.w - w) / 2, (clip.h - h) / 2
    return clip.crop(x1=x1, y1=y1, width=w, height=h)

# --- UI ---
st.set_page_config(page_title="AI Video Gen", layout="wide")
st.title("🎬 AI Video Generator")

script = st.text_area("📝 Paste your script:", height=150, placeholder="Enter your video script here...")
ratio = st.selectbox("📐 Select Ratio:", list(RATIOS.keys()))

if st.button("🚀 Generate Video", type="primary", use_container_width=True):
    if not script.strip():
        st.error("Please enter a script!")
    else:
        try:
            with st.spinner("🧠 AI is analyzing script..."):
                keywords = extract_keywords(script)
                st.write(f"**Visual Keywords:** {', '.join(keywords)}")
                
            with st.spinner("🎙️ Generating voiceover..."):
                audio_path = "voice.mp3"
                generate_audio_sync(script, audio_path)
                
            with st.spinner("🎬 Fetching stock videos..."):
                video_urls = []
                for kw in keywords:
                    url = search_pexels_videos(kw, PEXELS_API_KEY)
                    if url:
                        video_urls.append(url)
                
                if not video_urls:
                    st.error("Could not find videos. Check your Pexels API key.")
                    st.stop()
                    
            with st.spinner("✂️ Editing video..."):
                tw, th = RATIOS[ratio]
                audio_clip = AudioFileClip(audio_path)
                time_per = audio_clip.duration / len(video_urls)                
                clips = []
                progress_bar = st.progress(0)
                
                for i, url in enumerate(video_urls):
                    progress_bar.progress(i / len(video_urls))
                    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
                    try:
                        vid_data = requests.get(url, timeout=30)
                        with open(temp, 'wb') as f:
                            f.write(vid_data.content)
                        
                        v_clip = VideoFileClip(temp).subclip(0, min(time_per, 10))
                        clips.append(crop_and_resize(v_clip, tw, th))
                    except Exception as e:
                        st.warning(f"Skipping video {i+1}: {str(e)}")
                
                if clips:
                    final = concatenate_videoclips(clips, method="compose").set_audio(audio_clip)
                    final.write_videofile("output.mp4", codec="libx264", audio_codec="aac", fps=30, logger=None)
                    
                    st.success("✅ Video generated successfully!")
                    st.video("output.mp4")
                    
                    with open("output.mp4", "rb") as f:
                        st.download_button("⬇️ Download Video", f, file_name="video.mp4", mime="video/mp4")
                else:
                    st.error("No videos could be processed.")
                    
        except Exception as e:
            st.error(f"An error occurred: {str(e)}")
