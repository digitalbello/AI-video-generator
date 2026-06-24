import os
import requests
import tempfile
import shutil
import random
import json
import streamlit as st
import subprocess
from groq import Groq
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips

# --- Safety: Verify FFmpeg is available ---
if not shutil.which("ffmpeg"):
    st.warning("⚠️ System FFmpeg not found in PATH. Will try imageio-ffmpeg fallback.")

# --- Get API keys from Streamlit Cloud Secrets ---
try:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
    PEXELS_API_KEY = st.secrets["PEXELS_API_KEY"]
except KeyError as e:
    st.error(f"❌ Missing secret: {e}. Please add it in Streamlit Cloud Settings → Secrets.")
    st.stop()

RATIOS = {
    "9:16 (TikTok/Shorts)": (1080, 1920),
    "16:9 (YouTube)": (1920, 1080),
    "1:1 (Instagram)": (1080, 1080)
}

# --- Keyword Extraction ---
def extract_keywords(script):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""
    Extract exactly 3 simple visual keywords from the following script.
    Return ONLY a valid JSON object with keys "keyword1", "keyword2", "keyword3".
    Do not include any markdown formatting or explanation.
    
    Script: {script}
    """
    completion = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5
    )
    
    raw = completion.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    return list(data.values())

# --- Audio Generation (Using edge-tts CLI - NO ASYNC) ---
def generate_audio_sync(text, output_path):
    """Use edge-tts command line tool to avoid async issues."""
    import subprocess
    import sys
    
    cmd = [
        sys.executable, "-m", "edge_tts",
        "--text", text,
        "--voice", "en-US-AriaNeural",
        "--write-media", output_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        raise RuntimeError(f"edge-tts failed: {result.stderr}")
    
    if not os.path.exists(output_path):
        raise RuntimeError("Audio file was not created.")

# --- Stock Video Fetching (Direct Pexels API) ---
def get_stock_videos(keywords):
    urls = []
    headers = {"Authorization": PEXELS_API_KEY}
    
    for kw in keywords:
        try:
            response = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": kw, "per_page": 5},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            videos = data.get("videos", [])
            
            if videos:
                video_files = videos[0].get("video_files", [])
                if video_files:
                    sd_file = next(
                        (f for f in video_files if f.get("quality") == "sd"), 
                        video_files[0]
                    )
                    urls.append(sd_file["link"])
        except Exception as e:
            st.warning(f"⚠️ Could not fetch video for keyword '{kw}': {e}")
            continue
    
    return urls

# --- Video Processing ---
def crop_and_resize(clip, w, h):
    scale = max(w / clip.w, h / clip.h)
    clip = clip.resize(scale)
    x1 = (clip.w - w) / 2
    y1 = (clip.h - h) / 2
    return clip.crop(x1=x1, y1=y1, width=w, height=h)

# --- UI ---
st.set_page_config(page_title="AI Video Generator", page_icon="🎬")
st.title("🎬 AI Video Generator")

script = st.text_area("📝 Paste your script here:", height=150)
ratio = st.selectbox("📐 Select Ratio:", list(RATIOS.keys()))

if st.button("🚀 Generate Video", type="primary"):
    if not script.strip():
        st.error("Please enter a script!")
    else:
        temp_files = []
        try:
            # Step 1: Extract keywords
            with st.spinner("🧠 AI is analyzing script..."):
                keywords = extract_keywords(script)
                st.write(f"**Visuals:** {', '.join(keywords)}")
            
            # Step 2: Generate voice
            with st.spinner("🎙️ Generating voice..."):
                audio_path = "voice.mp3"
                generate_audio_sync(script, audio_path)
                temp_files.append(audio_path)
            
            # Step 3: Fetch stock videos
            with st.spinner("🎬 Fetching stock footage..."):
                vid_urls = get_stock_videos(keywords)
                if not vid_urls:
                    st.error("❌ No stock videos found. Try a different script.")
                    st.stop()
                st.write(f"**Found {len(vid_urls)} video clips**")
            
            # Step 4: Edit video
            with st.spinner("✂️ Editing video..."):
                tw, th = RATIOS[ratio]
                audio_clip = AudioFileClip(audio_path)
                time_per_clip = audio_clip.duration / len(vid_urls)
                
                clips = []
                for i, url in enumerate(vid_urls):
                    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                    temp_files.append(temp.name)
                    
                    with open(temp.name, "wb") as f:
                        vid_response = requests.get(url, timeout=30)
                        vid_response.raise_for_status()
                        f.write(vid_response.content)
                    
                    v_clip = VideoFileClip(temp.name)
                    max_duration = min(time_per_clip, v_clip.duration)
                    v_clip = v_clip.subclip(0, max_duration)
                    clips.append(crop_and_resize(v_clip, tw, th))
                
                # Concatenate and add audio
                final = concatenate_videoclips(clips, method="compose")
                final = final.set_audio(audio_clip)
                
                output_path = "output.mp4"
                final.write_videofile(
                    output_path, 
                    fps=24, 
                    codec="libx264", 
                    audio_codec="aac",
                    temp_audiofile="temp-audio.m4a",
                    remove_temp=True
                )
                temp_files.append(output_path)
                
                audio_clip.close()
                for c in clips:
                    c.close()
                final.close()
            
            st.success("✅ Done!")
            st.video(output_path)
            
            with open(output_path, "rb") as f:
                st.download_button(
                    "⬇️ Download Video", 
                    data=f, 
                    file_name="ai_video.mp4",
                    mime="video/mp4"
                )
                
        except Exception as e:
            st.error(f"❌ Error: {str(e)}")
            st.exception(e)
        finally:
            for f in temp_files:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except:
                    pass
