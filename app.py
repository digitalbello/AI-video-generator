import os
import requests
import tempfile
import shutil
import random
import json
import re
import streamlit as st
import subprocess
from groq import Groq
from moviepy import (
    ImageClip, AudioFileClip, concatenate_videoclips,
    TextClip, CompositeVideoClip, ColorClip
)
from moviepy.video.fx import CrossFadeIn, CrossFadeOut

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
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5
    )
    
    raw = completion.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    return list(data.values())

# --- Auto-Captions: Split script into timed segments ---
def generate_captions(script, audio_duration):
    """Split script into caption segments with timing."""
    text = script.strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if not sentences:
        return []
    
    time_per_sentence = audio_duration / len(sentences)
    captions = []
    
    for i, sentence in enumerate(sentences):
        start = i * time_per_sentence
        end = (i + 1) * time_per_sentence
        
        # Break long sentences into chunks of ~40 chars
        words = sentence.split()
        chunks = []
        current_chunk = ""
        for word in words:
            if len(current_chunk) + len(word) + 1 <= 40:
                current_chunk += " " + word if current_chunk else word
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = word
        if current_chunk:
            chunks.append(current_chunk)
        
        # Distribute chunks within sentence time
        chunk_duration = time_per_sentence / max(len(chunks), 1)
        for j, chunk in enumerate(chunks):
            c_start = start + (j * chunk_duration)
            c_end = start + ((j + 1) * chunk_duration)
            captions.append({
                "text": chunk,
                "start": c_start,
                "end": c_end
            })
    
    return captions

# --- Audio Generation (edge-tts CLI) ---
def generate_audio_sync(text, output_path):
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

# --- Background Music from Pexels ---
def get_background_music():
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        response = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={"query": "ambient background music", "per_page": 3},
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
                return sd_file["link"]
    except Exception:
        pass
    return None

def download_music(url, output_path):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)

# --- UI ---
st.set_page_config(page_title="AI Video Generator", page_icon="🎬")
st.title("🎬 AI Video Generator")
st.markdown("Create stunning videos from your images with voiceover, captions & music!")

script = st.text_area("📝 Paste your script here:", height=150)
ratio = st.selectbox("📐 Select Ratio:", list(RATIOS.keys()))

st.markdown("---")
st.subheader("📤 Upload Your Images")
uploaded_files = st.file_uploader(
    "Upload images (3-5 recommended)",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True
)

st.markdown("---")
add_captions = st.checkbox("📝 Add Auto-Captions", value=True)
add_music = st.checkbox("🎵 Add Background Music", value=True)
music_volume = st.slider("Music Volume", 0.0, 0.3, 0.08) if add_music else 0.08

if st.button("🚀 Generate Video", type="primary"):
    if not script.strip():
        st.error("Please enter a script!")
    elif not uploaded_files:
        st.error("Please upload at least one image!")
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
            
            # Step 3: Get audio duration and generate captions
            audio_clip = AudioFileClip(audio_path)
            audio_duration = audio_clip.duration
            
            captions = []
            if add_captions:
                with st.spinner("📝 Generating captions..."):
                    captions = generate_captions(script, audio_duration)
                    st.write(f"**Generated {len(captions)} caption segments**")
            
            # Step 4: Process uploaded images
            with st.spinner("🖼️ Processing images..."):
                image_paths = []
                for i, img_file in enumerate(uploaded_files):
                    temp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{i}.jpg")
                    temp.write(img_file.read())
                    temp.flush()
                    image_paths.append(temp.name)
                    temp_files.append(temp.name)
            
            # Step 5: Background music
            music_clip = None
            if add_music:
                with st.spinner("🎵 Fetching background music..."):
                    music_url = get_background_music()
                    if music_url:
                        music_path = "bg_music.mp4"
                        download_music(music_url, music_path)
                        temp_files.append(music_path)
                        music_clip = AudioFileClip(music_path)
                        # Loop if shorter than audio
                        if music_clip.duration < audio_duration:
                            loops = int(audio_duration / music_clip.duration) + 1
                            music_clip = concatenate_audioclips([music_clip] * loops)
                        music_clip = music_clip.subclipped(0, audio_duration)
                        music_clip = music_clip.with_volume_scaled(music_volume)
                    else:
                        st.warning("⚠️ Could not fetch background music. Continuing without it.")
            
            # Step 6: Create video
            with st.spinner("🎬 Creating video..."):
                tw, th = RATIOS[ratio]
                num_images = len(image_paths)
                time_per_image = audio_duration / num_images
                transition_duration = 0.8
                
                clips = []
                for i, img_path in enumerate(image_paths):
                    img_clip = ImageClip(img_path, duration=time_per_image)
                    
                    # Resize to fill frame
                    scale = max(tw / img_clip.w, th / img_clip.h)
                    img_clip = img_clip.resized(scale * 1.15)
                    
                    # Center crop
                    x1 = (img_clip.w - tw) / 2
                    y1 = (img_clip.h - th) / 2
                    img_clip = img_clip.cropped(x1=x1, y1=y1, width=tw, height=th)
                    
                    # Fade transitions
                    effects = []
                    if i > 0:
                        effects.append(CrossFadeIn(transition_duration))
                    if i < num_images - 1:
                        effects.append(CrossFadeOut(transition_duration))
                    if effects:
                        img_clip = img_clip.with_effects(effects)
                    
                    clips.append(img_clip)
                
                # Concatenate
                final = concatenate_videoclips(clips, method="compose")
                
                # Add captions
                if add_captions and captions:
                    caption_clips = []
                    for cap in captions:
                        txt = cap["text"]
                        start = cap["start"]
                        end = cap["end"]
                        duration = end - start
                        
                        # Caption text
                        txt_clip = TextClip(
                            text=txt,
                            font="Arial-Bold",
                            font_size=60,
                            color="white",
                            stroke_color="black",
                            stroke_width=3,
                            size=(tw - 100, None),
                            method="caption",
                            text_align="center"
                        )
                        txt_clip = txt_clip.with_duration(duration)
                        txt_clip = txt_clip.with_start(start)
                        txt_clip = txt_clip.with_position(("center", th * 0.75))
                        
                        # Black background bar
                        bar = ColorClip(
                            size=(tw, txt_clip.h + 30),
                            color=(0, 0, 0)
                        )
                        bar = bar.with_duration(duration)
                        bar = bar.with_start(start)
                        bar = bar.with_position(("center", th * 0.75 - 15))
                        bar = bar.with_opacity(0.6)
                        
                        caption_clips.extend([bar, txt_clip])
                    
                    final = CompositeVideoClip([final] + caption_clips)
                
                # Combine audio
                if music_clip:
                    from moviepy import CompositeAudioClip
                    combined_audio = CompositeAudioClip([audio_clip, music_clip])
                    final = final.with_audio(combined_audio)
                else:
                    final = final.with_audio(audio_clip)
                
                # Write video
                output_path = "output.mp4"
                final.write_videofile(
                    output_path,
                    fps=24,
                    codec="libx264",
                    audio_codec="aac",
                    threads=4,
                    preset="medium"
                )
                temp_files.append(output_path)
                
                # Cleanup
                audio_clip.close()
                if music_clip:
                    music_clip.close()
                for c in clips:
                    c.close()
                final.close()
            
            st.success("✅ Done! Your video is ready.")
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
    
