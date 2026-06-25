import os
import requests
import tempfile
import shutil
import random
import json
import re
import time
import textwrap
import streamlit as st
import subprocess
from groq import Groq
from moviepy import (
    ImageClip, AudioFileClip, concatenate_videoclips, concatenate_audioclips,
    TextClip, CompositeVideoClip, ColorClip, VideoClip
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

# ============ SCRIPT PARSER ============
def parse_script(raw_script):
    """
    Parse structured scripts with scenes, directions, and voiceovers.
    Returns list of scenes with metadata.
    """
    scenes = []
    
    # Split by SCENE headers
    scene_blocks = re.split(r'\n(?=SCENE\s+\d+)', raw_script.strip())
    
    # Handle intro text before first SCENE
    intro_text = ""
    if scene_blocks and not scene_blocks[0].strip().startswith("SCENE"):
        intro_text = scene_blocks[0].strip()
        scene_blocks = scene_blocks[1:]
    
    for block in scene_blocks:
        block = block.strip()
        if not block:
            continue
            
        # Extract scene name/timing
        scene_header = re.match(r'SCENE\s+\d+\s*[-\u2013\u2014]\s*(.+?)\s*\(([^)]+)\)', block)
        scene_name = scene_header.group(1).strip() if scene_header else "Scene"
        timing = scene_header.group(2).strip() if scene_header else "0-5s"
        
        # Extract visual directions [in brackets]
        directions = re.findall(r'\[([^\]]+)\]', block)
        visual_direction = directions[0] if directions else ""
        
        # Extract voiceover text
        voiceover_match = re.search(r'Voiceover:\s*"([^"]+)"', block, re.IGNORECASE)
        voiceover = voiceover_match.group(1) if voiceover_match else ""
        
        # Extract emotion/mood from directions
        emotion = extract_emotion(visual_direction)
        
        # Parse timing
        timing_parts = timing.replace("s", "").split("\u2013")
        if len(timing_parts) == 2:
            start_time = float(timing_parts[0].strip())
            end_time = float(timing_parts[1].strip())
            duration = end_time - start_time
        else:
            start_time = 0
            duration = 5
        
        scenes.append({
            "name": scene_name,
            "timing": timing,
            "start": start_time,
            "duration": duration,
            "visual_direction": visual_direction,
            "voiceover": voiceover,
            "emotion": emotion,
            "full_text": block
        })
    
    # If no scenes parsed, treat entire script as one scene
    if not scenes and raw_script.strip():
        scenes.append({
            "name": "Main Scene",
            "timing": "0-10s",
            "start": 0,
            "duration": 10,
            "visual_direction": raw_script.strip()[:100],
            "voiceover": raw_script.strip(),
            "emotion": "neutral",
            "full_text": raw_script.strip()
        })
    
    return scenes, intro_text

def extract_emotion(direction):
    """Extract emotional tone from visual directions."""
    direction_lower = direction.lower()
    emotions = {
        "urgent": ["fast", "quick", "rush", "panic", "alarm", "alert"],
        "calm": ["slow", "peaceful", "gentle", "soft", "smooth"],
        "dramatic": ["hard cut", "flash", "black screen", "dramatic", "intense"],
        "hopeful": ["bright", "light", "sunrise", "glow", "warm"],
        "sad": ["dark", "gloomy", "staring", "bills", "worried", "stress"],
        "exciting": ["zoom", "dynamic", "energy", "action", "movement"]
    }
    
    for emotion, keywords in emotions.items():
        if any(kw in direction_lower for kw in keywords):
            return emotion
    return "neutral"

# ============ AI IMAGE GENERATION ============
def generate_ai_image(prompt, width=1024, height=1024, seed=None):
    """Generate an image using Pollinations.ai free API."""
    if seed is None:
        seed = random.randint(1, 999999)
    
    encoded_prompt = requests.utils.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&seed={seed}&nologo=true"
    
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content

def build_image_prompt(scene, script_theme):
    """Build an emotionally rich image prompt from scene data."""
    emotion_keywords = {
        "urgent": "dynamic motion blur, high energy, dramatic lighting, cinematic action",
        "calm": "soft golden hour lighting, peaceful atmosphere, serene composition, gentle colors",
        "dramatic": "high contrast, dramatic shadows, cinematic lighting, intense atmosphere, film noir",
        "hopeful": "warm sunrise glow, bright optimistic lighting, golden rays, uplifting colors",
        "sad": "moody desaturated tones, soft melancholic lighting, emotional depth, cinematic drama",
        "exciting": "fast motion, dynamic angle, energetic composition, vibrant colors, action shot",
        "neutral": "professional photography, cinematic composition, high quality, 4k detailed"
    }
    
    base = scene["visual_direction"]
    emotion = scene.get("emotion", "neutral")
    emotion_style = emotion_keywords.get(emotion, emotion_keywords["neutral"])
    
    prompt = f"{base}, {emotion_style}, professional cinematography, photorealistic, 8k quality, film grain, color graded"
    
    return prompt

def generate_scene_images(scenes):
    """Generate AI images for each scene with emotion-aware prompts."""
    images = []
    
    for i, scene in enumerate(scenes):
        try:
            st.write(f"🎨 Generating scene {i+1}/{len(scenes)}: **{scene['name']}** ({scene['emotion']})")
            
            prompt = build_image_prompt(scene, "")
            img_data = generate_ai_image(prompt, width=1024, height=1024, seed=random.randint(1, 999999))
            
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_scene_{i}.png")
            temp.write(img_data)
            temp.flush()
            images.append(temp.name)
            
            time.sleep(1.5)
        except Exception as e:
            st.warning(f"⚠️ Scene {i+1} image failed: {e}")
            images.append(None)
    
    return images

# ============ MOTION EFFECTS (Make Images Feel Like Video) ============
def create_ken_burns_clip(img_path, duration, target_w, target_h, motion_type="zoom_in"):
    """
    Create a clip with Ken Burns motion effect to make still images feel alive.
    """
    img_clip = ImageClip(img_path, duration=duration)
    base_scale = max(target_w / img_clip.w, target_h / img_clip.h)
    
    # Motion parameters
    if motion_type == "zoom_in":
        start_scale = base_scale * 1.3
        end_scale = base_scale * 1.05
        start_x, start_y = 0.5, 0.5
        end_x, end_y = 0.5, 0.5
    elif motion_type == "zoom_out":
        start_scale = base_scale * 1.05
        end_scale = base_scale * 1.3
        start_x, start_y = 0.5, 0.5
        end_x, end_y = 0.5, 0.5
    elif motion_type == "pan_left":
        start_scale = base_scale * 1.2
        end_scale = base_scale * 1.2
        start_x, start_y = 0.7, 0.5
        end_x, end_y = 0.3, 0.5
    elif motion_type == "pan_right":
        start_scale = base_scale * 1.2
        end_scale = base_scale * 1.2
        start_x, start_y = 0.3, 0.5
        end_x, end_y = 0.7, 0.5
    elif motion_type == "pan_up":
        start_scale = base_scale * 1.2
        end_scale = base_scale * 1.2
        start_x, start_y = 0.5, 0.7
        end_x, end_y = 0.5, 0.3
    elif motion_type == "pan_down":
        start_scale = base_scale * 1.2
        end_scale = base_scale * 1.2
        start_x, start_y = 0.5, 0.3
        end_x, end_y = 0.5, 0.7
    else:
        start_scale = base_scale * 1.2
        end_scale = base_scale * 1.1
        start_x, start_y = 0.5, 0.5
        end_x, end_y = 0.5, 0.5
    
    def make_frame(t):
        progress = t / duration
        current_scale = start_scale + (end_scale - start_scale) * progress
        current_x = start_x + (end_x - start_x) * progress
        current_y = start_y + (end_y - start_y) * progress
        
        temp_clip = img_clip.resized(current_scale)
        
        crop_w = target_w
        crop_h = target_h
        center_x = current_x * temp_clip.w
        center_y = current_y * temp_clip.h
        
        x1 = center_x - crop_w / 2
        y1 = center_y - crop_h / 2
        x1 = max(0, min(x1, temp_clip.w - crop_w))
        y1 = max(0, min(y1, temp_clip.h - crop_h))
        
        return temp_clip.cropped(x1=x1, y1=y1, width=crop_w, height=crop_h).get_frame(t)
    
    motion_clip = VideoClip(make_frame, duration=duration)
    return motion_clip

def get_motion_for_scene(scene, index):
    """Determine motion type based on scene emotion and direction."""
    direction = scene.get("visual_direction", "").lower()
    emotion = scene.get("emotion", "neutral")
    
    if "fast zoom" in direction or "zoom" in direction:
        return "zoom_in"
    elif "slow-mo" in direction or "slow" in direction:
        return "pan_up"
    elif "flash" in direction or "cut" in direction:
        return "zoom_out"
    elif emotion == "urgent":
        return "zoom_in"
    elif emotion == "calm":
        return "pan_right"
    elif emotion == "dramatic":
        return "zoom_in"
    elif emotion == "hopeful":
        return "pan_up"
    elif emotion == "sad":
        return "pan_down"
    elif emotion == "exciting":
        return "pan_left"
    
    motions = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "pan_down"]
    return motions[index % len(motions)]

# ============ AUDIO & CAPTIONS ============
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

def generate_scene_captions(scenes):
    """Generate captions from scene voiceovers with timing."""
    captions = []
    
    for scene in scenes:
        voiceover = scene.get("voiceover", "")
        start = scene.get("start", 0)
        duration = scene.get("duration", 5)
        
        if not voiceover:
            continue
        
        words = voiceover.split()
        chunks = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 <= 35:
                current += " " + word if current else word
            else:
                if current:
                    chunks.append(current)
                current = word
        if current:
            chunks.append(current)
        
        chunk_duration = duration / max(len(chunks), 1)
        for j, chunk in enumerate(chunks):
            c_start = start + (j * chunk_duration)
            c_end = start + ((j + 1) * chunk_duration)
            captions.append({
                "text": chunk,
                "start": c_start,
                "end": c_end,
                "scene_name": scene.get("name", "")
            })
    
    return captions

# ============ BACKGROUND MUSIC ============
def get_background_music(emotion="neutral"):
    """Get background music matching the emotional tone."""
    emotion_queries = {
        "urgent": "intense dramatic action music",
        "calm": "peaceful ambient relaxing music",
        "dramatic": "cinematic dramatic tension music",
        "hopeful": "inspiring uplifting motivational music",
        "sad": "emotional melancholic piano music",
        "exciting": "energetic upbeat electronic music",
        "neutral": "ambient background music"
    }
    
    query = emotion_queries.get(emotion, "ambient background music")
    headers = {"Authorization": PEXELS_API_KEY}
    
    try:
        response = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={"query": query, "per_page": 3},
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

# ============ FONT ============
def get_available_font():
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            return path
    return None

# ============ UI ============
st.set_page_config(page_title="AI Video Generator Pro", page_icon="🎬")
st.title("🎬 AI Video Generator Pro")
st.markdown("🧠 **Intelligent Cinematic Mode**: Paste your script with scene directions, and AI creates a movie!")

st.markdown("""
**Script Format Example:**
 """)
script = st.text_area("📝 Paste your cinematic script:", height=250, 
    placeholder='Write your script with SCENE headers, [visual directions], and Voiceover: "text"')
ratio = st.selectbox("📐 Select Ratio:", list(RATIOS.keys()))

st.markdown("---")

image_source = st.radio(
    "🖼️ Image Source:",
    ["🤖 AI Generate Images from Script", "📤 Upload My Own Images"],
    index=0
)

uploaded_files = []
if image_source == "📤 Upload My Own Images":
    uploaded_files = st.file_uploader(
        "Upload one image per scene",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True
    )

st.markdown("---")
add_captions = st.checkbox("📝 Add Auto-Captions", value=True)
add_music = st.checkbox("🎵 Add Emotional Background Music", value=True)
music_volume = st.slider("Music Volume", 0.0, 0.3, 0.08) if add_music else 0.08

st.markdown("---")
st.markdown("**🎬 Motion Effects:**")
motion_intensity = st.slider("Motion Intensity", 0.0, 1.0, 0.7, 
    help="How much the images move (zoom/pan) to feel like video")

if st.button("🚀 Generate Cinematic Video", type="primary"):
    if not script.strip():
        st.error("Please enter a script!")
    elif image_source == "📤 Upload My Own Images" and not uploaded_files:
        st.error("Please upload images for each scene!")
    else:
        temp_files = []
        try:
            # Step 1: Parse script
            with st.spinner("🧠 Analyzing your cinematic script..."):
                scenes, intro_text = parse_script(script)
                st.write(f"**🎬 {len(scenes)} scenes detected:**")
                for s in scenes:
                    st.write(f"  • **{s['name']}** ({s['timing']}) — *{s['emotion']}* — \"{s['voiceover'][:50]}...\"")
            
            # Step 2: Generate full voiceover
            full_voiceover = " ".join([s["voiceover"] for s in scenes if s["voiceover"]])
            if not full_voiceover:
                full_voiceover = script
            
            with st.spinner("🎙️ Generating AI voiceover..."):
                audio_path = "voice.mp3"
                generate_audio_sync(full_voiceover, audio_path)
                temp_files.append(audio_path)
            
            # Step 3: Get audio duration
            audio_clip = AudioFileClip(audio_path)
            audio_duration = audio_clip.duration
            
            # Adjust scene durations based on actual audio
            total_voice_duration = sum(len(s["voiceover"].split()) for s in scenes if s["voiceover"])
            if total_voice_duration > 0:
                for s in scenes:
                    word_count = len(s["voiceover"].split())
                    s["duration"] = (word_count / total_voice_duration) * audio_duration
                    s["start"] = sum(scenes[j]["duration"] for j in range(scenes.index(s)))
            
            # Step 4: Generate captions
            captions = []
            if add_captions:
                with st.spinner("📝 Creating scene captions..."):
                    captions = generate_scene_captions(scenes)
                    st.write(f"**📝 {len(captions)} caption segments created**")
            
            # Step 5: Get images
            image_paths = []
            
            if image_source == "🤖 AI Generate Images from Script":
                with st.spinner("🎨 AI is generating cinematic images..."):
                    ai_images = generate_scene_images(scenes)
                    valid_images = [img for img in ai_images if img is not None]
                    if not valid_images:
                        st.error("❌ Failed to generate images. Please try again or upload your own.")
                        st.stop()
                    image_paths = valid_images
                    temp_files.extend(valid_images)
                    st.write(f"**✅ {len(valid_images)} cinematic images generated!**")
            else:
                with st.spinner("🖼️ Processing your images..."):
                    for i, img_file in enumerate(uploaded_files):
                        temp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{i}.jpg")
                        temp.write(img_file.read())
                        temp.flush()
                        image_paths.append(temp.name)
                        temp_files.append(temp.name)
            
            # Step 6: Background music matching emotion
            music_clip = None
            if add_music and scenes:
                dominant_emotion = max(set(s["emotion"] for s in scenes), 
                                      key=lambda e: sum(1 for s in scenes if s["emotion"] == e))
                
                with st.spinner(f"🎵 Finding {dominant_emotion} background music..."):
                    music_url = get_background_music(dominant_emotion)
                    if music_url:
                        music_path = "bg_music.mp4"
                        download_music(music_url, music_path)
                        temp_files.append(music_path)
                        music_clip = AudioFileClip(music_path)
                        if music_clip.duration < audio_duration:
                            loops = int(audio_duration / music_clip.duration) + 1
                            music_clip = concatenate_audioclips([music_clip] * loops)
                        music_clip = music_clip.subclipped(0, audio_duration)
                        music_clip = music_clip.with_volume_scaled(music_volume)
                    else:
                        st.warning("⚠️ Could not fetch background music.")
            
            # Step 7: Create cinematic video with motion
            with st.spinner("🎬 Assembling cinematic video with motion effects..."):
                tw, th = RATIOS[ratio]
                transition_duration = 0.5
                
                clips = []
                for i, (img_path, scene) in enumerate(zip(image_paths, scenes)):
                    motion_type = get_motion_for_scene(scene, i)
                    
                    scene_duration = scene.get("duration", audio_duration / len(image_paths))
                    img_clip = create_ken_burns_clip(
                        img_path, 
                        scene_duration, 
                        tw, th, 
                        motion_type=motion_type
                    )
                    
                    # Fade transitions
                    effects = []
                    if i > 0:
                        effects.append(CrossFadeIn(transition_duration))
                    if i < len(image_paths) - 1:
                        effects.append(CrossFadeOut(transition_duration))
                    if effects:
                        img_clip = img_clip.with_effects(effects)
                    
                    clips.append(img_clip)
                
                # Concatenate all scenes
                final = concatenate_videoclips(clips, method="compose")
                
                # Add scene title cards and captions
                if add_captions and captions:
                    overlay_clips = []
                    font_path = get_available_font()
                    
                    # Scene title cards
                    for i, scene in enumerate(scenes):
                        if scene.get("name") and scene["name"] != "Scene":
                            title_text = f"{scene['name'].upper()}"
                            title_kwargs = {
                                "text": title_text,
                                "font_size": 40,
                                "color": "white",
                                "stroke_color": "black",
                                "stroke_width": 2,
                                "size": (tw - 100, None),
                                "method": "caption",
                                "text_align": "center"
                            }
                            if font_path:
                                title_kwargs["font"] = font_path
                            
                            title_clip = TextClip(**title_kwargs)
                            title_clip = title_clip.with_duration(1.5)
                            title_clip = title_clip.with_start(scene["start"])
                            title_clip = title_clip.with_position(("center", th * 0.15))
                            
                            title_bar = ColorClip(size=(tw, title_clip.h + 20), color=(0, 0, 0))
                            title_bar = title_bar.with_duration(1.5)
                            title_bar = title_bar.with_start(scene["start"])
                            title_bar = title_bar.with_position(("center", th * 0.15 - 10))
                            title_bar = title_bar.with_opacity(0.5)
                            
                            overlay_clips.extend([title_bar, title_clip])
                    
                    # Captions
                    for cap in captions:
                        txt = cap["text"]
                        start = cap["start"]
                        end = cap["end"]
                        duration = end - start
                        
                        txt_kwargs = {
                            "text": txt,
                            "font_size": 55,
                            "color": "white",
                            "stroke_color": "black",
                            "stroke_width": 3,
                            "size": (tw - 120, None),
                            "method": "caption",
                            "text_align": "center"
                        }
                        if font_path:
                            txt_kwargs["font"] = font_path
                        
                        txt_clip = TextClip(**txt_kwargs)
                        txt_clip = txt_clip.with_duration(duration)
                        txt_clip = txt_clip.with_start(start)
                        txt_clip = txt_clip.with_position(("center", th * 0.78))
                        
                        cap_bar = ColorClip(size=(tw, txt_clip.h + 25), color=(0, 0, 0))
                        cap_bar = cap_bar.with_duration(duration)
                        cap_bar = cap_bar.with_start(start)
                        cap_bar = cap_bar.with_position(("center", th * 0.78 - 12))
                        cap_bar = cap_bar.with_opacity(0.65)
                        
                        overlay_clips.extend([cap_bar, txt_clip])
                    
                    final = CompositeVideoClip([final] + overlay_clips)
                
                # Combine audio
                if music_clip:
                    from moviepy import CompositeAudioClip
                    combined_audio = CompositeAudioClip([audio_clip, music_clip])
                    final = final.with_audio(combined_audio)
                else:
                    final = final.with_audio(audio_clip)
                
                # Write final video
                output_path = "output.mp4"
                final.write_videofile(
                    output_path,
                    fps=30,
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
            
            st.success("✅ Your cinematic video is ready! 🎬")
            
            # Read video as bytes for Streamlit display
            with open(output_path, "rb") as video_file:
                video_bytes = video_file.read()
            st.video(video_bytes)
            
            st.download_button(
                "⬇️ Download Cinematic Video",
                data=video_bytes,
                file_name="cinematic_ai_video.mp4",
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
        
