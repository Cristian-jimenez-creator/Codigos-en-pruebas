import customtkinter as ctk
import ollama
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import json
import random
import shutil
from datetime import datetime

import pygame
import pyautogui
import speech_recognition as sr
from PIL import Image, ImageGrab

# =========================
# NOVA 3D
# =========================
NOVA3D_AVAILABLE = False
try:
    from nova_3d_controller import Nova3D
    NOVA3D_AVAILABLE = True
except ImportError as e:
    print(f"[NOVA] Nova 3D no disponible: {e}")

# =========================
# Perfiles
# =========================
PERFILES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perfiles_nova.json")

def cargar_perfiles():
    if os.path.exists(PERFILES_PATH):
        with open(PERFILES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def guardar_perfiles(perfiles):
    with open(PERFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(perfiles, f, ensure_ascii=False, indent=2)

# =========================
# HOTKEY GLOBAL (TECLA 5) - PTT
# =========================
try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False

# =========================
# CONFIGURACIÓN TTS / STT
# =========================
PIPER_MODEL = "es_AR-daniela-high.onnx"
PIPER_CONFIG = "es_AR-daniela-high.onnx.json"
PIPER_AVAILABLE = False
VOSK_AVAILABLE = False
PiperVoiceClass = None

def _try_import_piper():
    global PIPER_AVAILABLE, PiperVoiceClass
    for stmt in ["from piper import PiperVoice", "from piper.voice import PiperVoice"]:
        try:
            exec(f"{stmt}; PiperVoiceClass = PiperVoice", globals())
            PIPER_AVAILABLE = True
            return
        except Exception:
            pass
    try:
        import piper
        if hasattr(piper, 'PiperVoice'):
            PiperVoiceClass = piper.PiperVoice
            PIPER_AVAILABLE = True
        elif hasattr(piper, 'voice') and hasattr(piper.voice, 'PiperVoice'):
            PiperVoiceClass = piper.voice.PiperVoice
            PIPER_AVAILABLE = True
    except Exception:
        pass

_try_import_piper()

try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False

VOSK_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vosk-model-small-es-0.42")

try:
    import pyaudio
    import numpy as np
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    np = None

try:
    import sounddevice as sd
    import soundfile as sf
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False

try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False

# =========================
# CONFIGURACIÓN DE MENTE
# =========================
MEMORIA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova_memoria.json")

ESTADOS_ANIMO = {
    "neutral": {"emoji": "😐"},
    "feliz": {"emoji": "😊"},
    "curiosa": {"emoji": "🤔"},
    "cansada": {"emoji": "😴"},
    "emocionada": {"emoji": "🤩"},
    "tranquila": {"emoji": "😌"},
}


class MenteNova:
    def __init__(self):
        self.memoria = self.cargar_memoria()
        self.estado_animo = self.memoria.get("ultimo_animo", "neutral")
        self.primera_vez_hoy = False

    def cargar_memoria(self):
        if os.path.exists(MEMORIA_PATH):
            try:
                with open(MEMORIA_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "user_name": "", "conversaciones_hoy": 0, "total_conversaciones": 0,
            "ultima_fecha": "", "ultimo_animo": "neutral", "temas_favoritos": [],
            "ultima_interaccion": "", "notas_usuario": [], "mic_device_index": None
        }

    def guardar_memoria(self):
        with open(MEMORIA_PATH, "w", encoding="utf-8") as f:
            json.dump(self.memoria, f, ensure_ascii=False, indent=2)

    def registrar_interaccion(self, texto_usuario, respuesta_nova):
        hoy = datetime.now().strftime("%Y-%m-%d")
        ahora = datetime.now().strftime("%H:%M")

        if self.memoria["ultima_fecha"] != hoy:
            self.memoria["conversaciones_hoy"] = 0
            self.primera_vez_hoy = True
            self.memoria["ultima_fecha"] = hoy
        else:
            self.primera_vez_hoy = False

        self.memoria["conversaciones_hoy"] += 1
        self.memoria["total_conversaciones"] += 1
        self.memoria["ultima_interaccion"] = ahora

        temas = ["música", "juego", "python", "trabajo", "película", "comida", "code", "programar"]
        for t in temas:
            if t in texto_usuario.lower() and t not in self.memoria["temas_favoritos"]:
                self.memoria["temas_favoritos"].append(t)
                if len(self.memoria["temas_favoritos"]) > 20:
                    self.memoria["temas_favoritos"].pop(0)

        self.ajustar_animo(texto_usuario)
        self.memoria["ultimo_animo"] = self.estado_animo
        self.guardar_memoria()

    def ajustar_animo(self, texto):
        t = texto.lower()
        if any(x in t for x in ["gracias", "bien", "genial", "excelente", "me gusta", "perfecto"]):
            self.estado_animo = "feliz"
        elif any(x in t for x in ["¿", "como", "por qué", "qué es", "cuál", "dime"]):
            self.estado_animo = "curiosa"
        elif any(x in t for x in ["aburrido", "cansado", "malo", "odio", "estresado"]):
            self.estado_animo = "tranquila"
        elif any(x in t for x in ["wow", "increíble", "epico", "brutal", "novedad"]):
            self.estado_animo = "emocionada"
        else:
            self.estado_animo = "neutral"

    def generar_contexto_sistema(self):
        hora = datetime.now().hour
        if 5 <= hora < 12:
            saludo_contexto = "Es mañana."
        elif 12 <= hora < 18:
            saludo_contexto = "Es tarde."
        else:
            saludo_contexto = "Es noche."

        nombre = self.memoria.get("user_name", "")
        nombre_texto = f"El usuario se llama {nombre}." if nombre else ""
        temas = ", ".join(self.memoria["temas_favoritos"][-3:]) if self.memoria["temas_favoritos"] else "aún desconocidos"
        notas = ""
        if self.memoria["notas_usuario"]:
            notas = "Recuerdos: " + "; ".join(self.memoria["notas_usuario"][-2:])

        perfiles = cargar_perfiles()
        perfil_usuario = ""
        perfil_nova = ""

        if perfiles:
            user = perfiles.get("usuario", {})
            nova = perfiles.get("nova", {})

            datos_user = []
            if user.get("nombre_preferido"):
                datos_user.append(f"Se llama {user['nombre_preferido']}")
            elif user.get("nombre"):
                datos_user.append(f"Se llama {user['nombre']}")
            if user.get("personalidad", {}).get("rasgos_destacados"):
                datos_user.append(f"Rasgos: {', '.join(user['personalidad']['rasgos_destacados'])}")
            if user.get("preferencias", {}).get("musica"):
                datos_user.append(f"Le gusta: {', '.join(user['preferencias']['musica'])}")
            if user.get("relacion_con_nova", {}).get("como_me_llama"):
                datos_user.append(f"Me llama: {user['relacion_con_nova']['como_me_llama']}")

            if datos_user:
                perfil_usuario = "PERFIL: " + "; ".join(datos_user) + "."

            perfil_nova = f"""SOY {nova.get('nombre', 'Nova')}. {nova.get('personalidad', {}).get('descripcion', '')}"""

        prompt = f"""Eres Nova. Respuestas de UNA SOLA ORACIÓN. Nunca en inglés. {saludo_contexto} {nombre_texto}
Temas: {temas}. {notas} Ánimo: {self.estado_animo}.
{perfil_usuario} {perfil_nova}
REGLA: Máximo 15 palabras. Sé directa."""

        return prompt

    def obtener_saludo_inicial(self):
        if not self.primera_vez_hoy:
            return None
        hora = datetime.now().hour
        nombre = self.memoria.get("user_name", "")
        saludo_nombre = f" {nombre}" if nombre else ""
        if 5 <= hora < 12:
            return f"Buenos días{saludo_nombre}. ¿En qué andamos?"
        elif 12 <= hora < 18:
            return f"Buenas tardes{saludo_nombre}. ¿Qué necesitas?"
        else:
            return f"Buenas noches{saludo_nombre}. ¿Trabajamos?"

    def recordar(self, hecho):
        if hecho not in self.memoria["notas_usuario"]:
            self.memoria["notas_usuario"].append(hecho)
            if len(self.memoria["notas_usuario"]) > 10:
                self.memoria["notas_usuario"].pop(0)
            self.guardar_memoria()

    def actualizar_perfil_usuario(self, clave, valor):
        perfiles = cargar_perfiles()
        if not perfiles:
            return
        try:
            keys = clave.split(".")
            target = perfiles["usuario"]
            for k in keys[:-1]:
                target = target.setdefault(k, {})
            actual = target.get(keys[-1])
            if isinstance(actual, list):
                if valor not in actual:
                    actual.append(valor)
            else:
                target[keys[-1]] = valor
            guardar_perfiles(perfiles)
        except Exception as e:
            print(f"[NOVA] Error perfil: {e}")


class NovaApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # VENTANA INVISIBLE
        self.title("Nova")
        self.geometry("1x1+9999+9999")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#000000")
        self.attributes("-transparentcolor", "#000000")
        self.withdraw()

        self.base_dir = os.path.dirname(os.path.abspath(__file__))

        self.nova3d = None
        if NOVA3D_AVAILABLE:
            try:
                self.nova3d = Nova3D(self.base_dir, width=380, height=480, x=100, y=100)
                self.nova3d.start()
                print("[NOVA] Cuerpo 3D iniciando...")
            except Exception as e:
                print(f"[NOVA] Error 3D: {e}")

        self.is_speaking = False
        self.is_processing = False
        self.is_listening = False
        self.closing = False

        # ========== MODELOS ==========
        self.model_chat = "llama3.2:1b"
        self.model_vision = "llava-phi3:latest"
        self.ollama_available = self._check_ollama()

        self._precargar_modelos()

        self.vosk_model = None
        if VOSK_AVAILABLE and os.path.exists(VOSK_MODEL_PATH):
            try:
                self.vosk_model = Model(VOSK_MODEL_PATH)
                print(f"[NOVA] Vosk precargado")
            except Exception as e:
                print(f"[NOVA] Error Vosk: {e}")

        self.mente = MenteNova()
        self.historial = [{"role": "system", "content": self.mente.generar_contexto_sistema()}]
        self.eventos = queue.Queue()

        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = False
        self.recognizer.energy_threshold = 30
        self.recognizer.pause_threshold = 1.2
        self.recognizer.phrase_threshold = 0.2

        self.microphone = None
        self.mic_available = False
        self.mic_calibrado = False
        self.mic_device_index = self.mente.memoria.get("mic_device_index", None)

        self.ptt_active = False
        self.ptt_frames = []
        self.ptt_thread = None
        self.audio_pyaudio = None
        self.audio_rate = 16000
        self.audio_channels = 1
        self.audio_format = None
        self.audio_chunk = 1024

        if PYAUDIO_AVAILABLE:
            try:
                self.audio_pyaudio = pyaudio.PyAudio()
                self.audio_format = pyaudio.paInt16
            except Exception as e:
                print(f"[NOVA] PyAudio error: {e}")

        self.hotkey_listener = None

        self.piper_modelo_path = os.path.join(self.base_dir, PIPER_MODEL)
        self.piper_config_path = os.path.join(self.base_dir, PIPER_CONFIG)
        self.piper_voice = None
        self._tts_fallo_permanente = False

        # ========== AUDIO CON FADE-OUT PARA EVITAR RUIDO ==========
        self._audio_fadeout_ms = 300  # Fade-out de 300ms antes de detener
        # Inicializar pygame mixer a 22050Hz (frecuencia nativa de Piper)
        # buffer=2048 evita underruns que suenan como "televisor"/estático
        try:
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=2048)
            print("[NOVA] Audio inicializado a 22050Hz, buffer 2048")
        except Exception as e:
            print(f"[NOVA] Audio init: {e}")
            try:
                pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=2048)
            except:
                pass

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ========== MANEJO DE SEÑALES PARA CIERRE CON CTRL+C ==========
        import signal
        def signal_handler(sig, frame):
            print("\n[NOVA] Señal de interrupción recibida. Cerrando...")
            self.on_close()
        signal.signal(signal.SIGINT, signal_handler)
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, signal_handler)

        self.after(500, self.calibrar_microfono_inicio)
        self.after(700, self.iniciar_hotkey_ptt)
        self.after(900, self.procesar_cola)
        self.after(400, self._verificar_tts)
        self.after(2500, self.saludo_inicial)

    def _check_ollama(self):
        try:
            ollama.list()
            print("[NOVA] ✅ Ollama OK")
            return True
        except Exception as e:
            print("[NOVA] 🚨 Ollama offline")
            return False

    def _precargar_modelos(self):
        if not self.ollama_available:
            return
        for modelo in [self.model_chat, self.model_vision]:
            try:
                ollama.generate(model=modelo, prompt="hola", options={"num_predict": 1}, keep_alive=-1)
                print(f"[NOVA] Modelo {modelo} precargado en memoria")
            except Exception as e:
                print(f"[NOVA] No se pudo precargar {modelo}: {e}")

    def _verificar_tts(self):
        print("[NOVA] Verificando TTS...")
        faltantes = []
        if not os.path.exists(self.piper_modelo_path):
            faltantes.append(f"Modelo .onnx")
        if not os.path.exists(self.piper_config_path):
            faltantes.append(f"Config .json")

        if faltantes:
            print(f"[NOVA] Faltan: {faltantes}")
            self._tts_fallo_permanente = True
            return

        self.piper_cli_path = shutil.which("piper") or shutil.which("piper.exe")

        if PiperVoiceClass:
            try:
                self.piper_voice = PiperVoiceClass.load(self.piper_modelo_path)
                print("[NOVA] Modelo TTS precargado")
            except Exception as e:
                print(f"[NOVA] No se pudo precargar TTS: {e}")

        prueba_path = os.path.join(tempfile.gettempdir(), "nova_tts_prueba.wav")
        prueba_ok = False

        if getattr(self, 'piper_cli_path', None):
            try:
                cmd = [
                    self.piper_cli_path,
                    "--model", self.piper_modelo_path,
                    "--output_file", prueba_path,
                    "--length-scale", "1.0",
                    "--noise-scale", "0.333",
                    "--noise-w", "0.333",
                    "--sentence-silence", "0.0"
                ]
                if os.path.exists(self.piper_config_path):
                    cmd.extend(["--config", self.piper_config_path])
                proc = subprocess.run(cmd, input="Hola, soy Nova.".encode(), capture_output=True, timeout=15)
                if proc.returncode == 0 and os.path.exists(prueba_path) and os.path.getsize(prueba_path) > 0:
                    prueba_ok = True
            except Exception:
                pass

        if os.path.exists(prueba_path):
            try:
                os.remove(prueba_path)
            except:
                pass

        if prueba_ok:
            print("[NOVA] TTS Daniela listo")
        else:
            print("[NOVA] TTS falló prueba")
            self._tts_fallo_permanente = True

    def _tts_sintetizar_chunk(self, texto, output_path):
        if getattr(self, 'piper_cli_path', None) and os.path.exists(self.piper_modelo_path):
            try:
                cmd = [
                    self.piper_cli_path,
                    "--model", self.piper_modelo_path,
                    "--output_file", output_path,
                    "--length-scale", "1.0",
                    "--noise-scale", "0.333",
                    "--noise-w", "0.333",
                    "--sentence-silence", "0.0"
                ]
                if os.path.exists(self.piper_config_path):
                    cmd.extend(["--config", self.piper_config_path])
                proc = subprocess.run(
                    cmd,
                    input=texto.encode("utf-8"),
                    capture_output=True,
                    timeout=max(8, min(30, len(texto) * 0.5))
                )
                if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                    return True, "cli"
            except Exception:
                pass

        if self.piper_voice:
            try:
                with open(output_path, "wb") as f:
                    self.piper_voice.synthesize(texto, f)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                    return True, "lib"
            except Exception:
                pass

        if PiperVoiceClass and os.path.exists(self.piper_modelo_path):
            try:
                voice = PiperVoiceClass.load(self.piper_modelo_path)
                with open(output_path, "wb") as f:
                    voice.synthesize(texto, f)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                    return True, "lib_dyn"
            except Exception:
                pass

        return False, None

    def _tts_fade_wav(self, wav_path, fade_in_ms=80, fade_out_ms=120):
        """Aplica fade-in/fade-out digital al WAV para eliminar pops y ruido de borde."""
        if np is None:
            return wav_path
        try:
            import wave
            with wave.open(wav_path, 'rb') as wf:
                nchannels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                nframes = wf.getnframes()
                raw = wf.readframes(nframes)

            # .copy() es CRÍTICO: np.frombuffer devuelve array read-only
            audio = np.frombuffer(raw, dtype=np.int16).copy()
            if nchannels > 1:
                audio = audio.reshape(-1, nchannels)[:, 0]  # convertir a mono

            fade_in_samples = int(framerate * fade_in_ms / 1000)
            fade_out_samples = int(framerate * fade_out_ms / 1000)

            if len(audio) > fade_in_samples + fade_out_samples:
                # Fade in
                audio[:fade_in_samples] = (audio[:fade_in_samples] * np.linspace(0, 1, fade_in_samples)).astype(np.int16)
                # Fade out
                audio[-fade_out_samples:] = (audio[-fade_out_samples:] * np.linspace(1, 0, fade_out_samples)).astype(np.int16)

            # Guardar temporal
            tmp_path = wav_path.replace('.wav', '_fade.wav')
            with wave.open(tmp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(framerate)
                wf.writeframes(audio.tobytes())
            return tmp_path
        except Exception as e:
            print(f"[NOVA] Fade WAV error: {e}")
            return wav_path

    def _tts_reproducir_wav(self, wav_path):
        # Aplicar fade digital antes de reproducir
        wav_to_play = self._tts_fade_wav(wav_path) if np is not None else wav_path

        # === PRIORIDAD 1: sounddevice (mejor calidad, sin ruido de buffer) ===
        if SOUNDDEVICE_AVAILABLE:
            try:
                data, samplerate = sf.read(wav_to_play, dtype='float32')
                sd.play(data, samplerate)
                duracion = len(data) / samplerate
                t0 = time.time()
                while time.time() - t0 < duracion and not self.closing and self.is_speaking:
                    time.sleep(0.05)
                sd.stop()
                # Limpiar archivo fade temporal si se creó
                if wav_to_play != wav_path and os.path.exists(wav_to_play):
                    try:
                        os.remove(wav_to_play)
                    except:
                        pass
                return True
            except Exception:
                pass

        # === PRIORIDAD 2: pygame.mixer.Sound (mejor que mixer.music para TTS) ===
        try:
            if pygame.mixer.get_init():
                sound = pygame.mixer.Sound(wav_to_play)
                channel = sound.play()
                if channel:
                    while channel.get_busy() and not self.closing and self.is_speaking:
                        time.sleep(0.03)
                    # Fade-out suave en el canal
                    if channel.get_busy():
                        channel.fadeout(self._audio_fadeout_ms)
                        time.sleep(self._audio_fadeout_ms / 1000.0 + 0.05)
                    channel.stop()
                sound.stop()
                # Limpiar archivo fade temporal si se creó
                if wav_to_play != wav_path and os.path.exists(wav_to_play):
                    try:
                        os.remove(wav_to_play)
                    except:
                        pass
                return True
        except Exception:
            pass

        # === PRIORIDAD 3: winsound (Windows nativo) ===
        if WINSOUND_AVAILABLE:
            try:
                winsound.PlaySound(wav_to_play, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                # Limpiar archivo fade temporal si se creó
                if wav_to_play != wav_path and os.path.exists(wav_to_play):
                    try:
                        os.remove(wav_to_play)
                    except:
                        pass
                return True
            except Exception:
                pass

        # Limpiar archivo fade temporal si se creó y no se reprodujo
        if wav_to_play != wav_path and os.path.exists(wav_to_play):
            try:
                os.remove(wav_to_play)
            except:
                pass
        return False

    def limpiar_para_tts(self, texto):
        texto = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+', '', texto)
        texto = re.sub(r'[*_`~#]', '', texto)
        texto = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', texto)
        texto = re.sub(r'https?://\S+', '', texto)
        texto = re.sub(r'[^\w\sáéíóúüñÁÉÍÓÚÜÑ.,;:!?¿¡\-\'\"]', '', texto)
        texto = re.sub(r'\s+', ' ', texto).strip()
        return texto

    def hablar(self, texto):
        """TTS con fade-out suave para evitar ruido de cierre."""
        texto = self.limpiar_texto(texto)
        texto = self.limpiar_para_tts(texto)
        if not texto:
            return

        if self._tts_fallo_permanente:
            print(f"[NOVA] 📄 {texto[:50]}...")
            return

        def _tts():
            self.is_speaking = True
            self.mostrar_estado("speaking")
            print("[NOVA] Hablando...")

            if self.nova3d:
                try:
                    self.nova3d.speaking(True)
                except:
                    pass

            audio_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                    audio_path = tmp.name

                ok, _ = self._tts_sintetizar_chunk(texto, audio_path)
                if ok:
                    self._tts_reproducir_wav(audio_path)
                else:
                    print("[NOVA] 🚨 TTS falló")
                    self._tts_fallo_permanente = True
            except Exception as e:
                print(f"[NOVA] TTS error: {e}")
            finally:
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except:
                        pass

            if self.nova3d:
                try:
                    self.nova3d.speaking(False)
                except:
                    pass

            self.is_speaking = False
            if not self.is_processing:
                self.mostrar_estado("idle")
                print("[NOVA] Listo. Mantén 5 para hablar." if self.mic_available else "[NOVA] Sin micrófono.")

        threading.Thread(target=_tts, daemon=True).start()

    def set_status(self, texto):
        print(f"[NOVA] {texto}")

    def mostrar_estado(self, estado):
        if not self.nova3d:
            return
        mapeo = {
            "idle": "idle", "escuchando": "listening", "pensando": "thinking",
            "speaking": "speaking", "error": "error",
        }
        try:
            self.nova3d.state(mapeo.get(estado, "idle"))
        except:
            pass

    def saludo_inicial(self):
        if self.nova3d:
            for _ in range(25):
                if self.nova3d.is_ready():
                    break
                time.sleep(0.2)
        saludo = self.mente.obtener_saludo_inicial()
        if saludo:
            self.hablar(saludo)

    def _obtener_tasa_soportada(self, device_index):
        if not PYAUDIO_AVAILABLE or self.audio_pyaudio is None:
            return None
        try:
            info = self.audio_pyaudio.get_device_info_by_index(device_index)
            default_rate = int(info.get('defaultSampleRate', 44100))
            for rate in [16000, default_rate, 44100, 48000]:
                try:
                    test_stream = self.audio_pyaudio.open(
                        format=self.audio_format, channels=1, rate=rate,
                        input=True, input_device_index=device_index, frames_per_buffer=1024
                    )
                    test_stream.close()
                    return rate
                except Exception:
                    pass
            return None
        except Exception:
            return None

    def listar_microfonos_disponibles(self):
        try:
            return sr.Microphone.list_microphone_names()
        except Exception as e:
            print(f"[NOVA] Error listando mics: {e}")
            return []

    def _es_dispositivo_fantasma(self, nombre):
        if not nombre or len(nombre.strip()) < 6:
            return True
        nombre_lower = nombre.lower().strip()
        virtuales = ["asignador", "mapper", "stereo mix", "mezcla est", "output",
                     "altavoz", "speaker", "headphones", "altavoces", "controlador primario"]
        return any(v in nombre_lower for v in virtuales)

    def elegir_microfono(self):
        try:
            mics = self.listar_microfonos_disponibles()
            if not mics:
                raise Exception("No se detectaron micrófonos")
            candidatos = []
            for i, nombre in enumerate(mics):
                if self._es_dispositivo_fantasma(nombre):
                    continue
                tasa = self._obtener_tasa_soportada(i)
                if tasa is None:
                    continue
                if "micrófono" in nombre.lower() or "microphone" in nombre.lower():
                    candidatos.insert(0, (i, nombre, tasa))
                else:
                    candidatos.append((i, nombre, tasa))
            if not candidatos and mics:
                tasa = self._obtener_tasa_soportada(0)
                if tasa:
                    candidatos.append((0, mics[0], tasa))
            return candidatos
        except Exception as e:
            print(f"[NOVA] Error eligiendo mic: {e}")
            raise

    def calibrar_microfono_inicio(self):
        try:
            print("[NOVA] Buscando micrófono...")
            candidatos = self.elegir_microfono()
            for i, nombre, tasa in candidatos:
                try:
                    mic = sr.Microphone(device_index=i)
                    with mic as source:
                        self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                        self.recognizer.energy_threshold = max(30, self.recognizer.energy_threshold * 0.6)
                    self.microphone = mic
                    self.mic_device_index = i
                    self.audio_rate = tasa
                    self.mic_available = True
                    self.mic_calibrado = True
                    print("[NOVA] Listo. Mantén 5 para hablar.")
                    self.mente.memoria["mic_device_index"] = i
                    self.mente.guardar_memoria()
                    self.after(500, self._test_captura_microfono)
                    return
                except Exception as e:
                    print(f"[NOVA] Mic [{i}] falló: {e}")
            raise Exception("Ningún micrófono funcional")
        except Exception as e:
            print(f"[NOVA] Error calibrando: {e}")
            print("[NOVA] Sin micrófono.")
            self.mic_available = False

    def _test_captura_microfono(self):
        if not self.mic_available or not PYAUDIO_AVAILABLE or np is None:
            return
        try:
            stream = self.audio_pyaudio.open(
                format=self.audio_format, channels=self.audio_channels,
                rate=self.audio_rate, input=True, input_device_index=self.mic_device_index,
                frames_per_buffer=self.audio_chunk
            )
            frames = []
            for _ in range(int(self.audio_rate / self.audio_chunk * 2)):
                data = stream.read(self.audio_chunk, exception_on_overflow=False)
                frames.append(data)
            stream.stop_stream()
            stream.close()
            raw = b''.join(frames)
            audio_np = np.frombuffer(raw, dtype=np.int16)
            max_amp = np.max(np.abs(audio_np))
            if max_amp < 100:
                print("[NOVA] 🚨 Mic sin señal")
                self.mic_device_index = None
                self.reintentar_microfono()
            else:
                print("[NOVA] Mic capturando OK")
        except Exception as e:
            print(f"[NOVA] Test captura error: {e}")

    def reintentar_microfono(self):
        print("[NOVA] Reconectando...")
        self.mic_calibrado = False
        self.microphone = None
        self.mic_device_index = None
        self.mente.memoria["mic_device_index"] = None
        self.mente.guardar_memoria()
        self.after(100, self.calibrar_microfono_inicio)

    def iniciar_hotkey_ptt(self):
        if not PYNPUT_AVAILABLE:
            print("[NOVA] Mantén 5 (foco en ventana)")
            return

        def on_press(key):
            try:
                if hasattr(key, 'char') and key.char == '5' and not self.ptt_active:
                    self.iniciar_ptt()
            except Exception:
                pass

        def on_release(key):
            try:
                if hasattr(key, 'char') and key.char == '5' and self.ptt_active:
                    self.detener_ptt()
            except Exception:
                pass

        self.hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.hotkey_listener.start()
        print("[NOVA] Listo. Mantén 5 para hablar.")

    def iniciar_ptt(self):
        if self.closing or not self.mic_available or self.is_processing:
            return
        self.ptt_active = True
        self.ptt_frames = []
        self.is_listening = True
        self.mostrar_estado("escuchando")
        print("[NOVA] 🎙️ Escuchando... (suelta 5)")
        if PYAUDIO_AVAILABLE and self.audio_pyaudio:
            self.ptt_thread = threading.Thread(target=self._ptt_record_pyaudio, daemon=True)
        else:
            self.ptt_thread = threading.Thread(target=self._ptt_record_hybrid, daemon=True)
        self.ptt_thread.start()

    def detener_ptt(self):
        self.ptt_active = False

    def _ptt_record_pyaudio(self):
        stream = None
        try:
            stream = self.audio_pyaudio.open(
                format=self.audio_format, channels=self.audio_channels,
                rate=self.audio_rate, input=True, input_device_index=self.mic_device_index,
                frames_per_buffer=self.audio_chunk
            )
            while self.ptt_active:
                try:
                    data = stream.read(self.audio_chunk, exception_on_overflow=False)
                    self.ptt_frames.append(data)
                except Exception:
                    break
        except Exception as e:
            print(f"[NOVA] Stream PTT error: {e}")
            self.ptt_active = False
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except:
                    pass
        self._procesar_audio_ptt()

    def _ptt_record_hybrid(self):
        audios = []
        try:
            with self.microphone as source:
                while self.ptt_active:
                    try:
                        audio = self.recognizer.listen(source, timeout=1.0, phrase_time_limit=0.8)
                        audios.append(audio)
                    except sr.WaitTimeoutError:
                        continue
        except Exception as e:
            print(f"[NOVA] Híbrido error: {e}")
        if audios:
            combined_raw = b''.join([a.get_raw_data() for a in audios])
            audio_temp = sr.AudioData(combined_raw, audios[0].sample_rate, audios[0].sample_width)
            wav_16k = self._preparar_audio_vosk(audio_temp)
            if wav_16k:
                self.eventos.put(sr.AudioData(wav_16k, 16000, 2))
        self.is_listening = False
        if not self.is_processing and not self.is_speaking and not self.closing:
            self.mostrar_estado("idle")
            print("[NOVA] Listo. Mantén 5 para hablar." if self.mic_available else "[NOVA] Sin micrófono.")

    def _procesar_audio_ptt(self):
        if not self.ptt_frames:
            self.is_listening = False
            print("[NOVA] Muy corto. Mantén 5.")
            return
        raw_audio = b''.join(self.ptt_frames)
        if self.audio_rate != 16000 and np is not None:
            try:
                audio_np = np.frombuffer(raw_audio, dtype=np.int16)
                ratio = 16000 / self.audio_rate
                new_len = int(len(audio_np) * ratio)
                if new_len > 0:
                    indices = np.linspace(0, len(audio_np) - 1, new_len)
                    audio_np = np.interp(indices, np.arange(len(audio_np)), audio_np)
                    audio_np = np.clip(audio_np, -32768, 32767).astype(np.int16)
                    raw_audio = audio_np.tobytes()
            except Exception as e:
                print(f"[NOVA] Re-muestreo error: {e}")
        if np is not None:
            try:
                audio_np = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32)
                audio_np = np.clip(audio_np * 3.0, -32768, 32767).astype(np.int16)
                raw_audio = audio_np.tobytes()
            except Exception:
                pass
        try:
            import noisereduce as nr
            if np is not None:
                audio_np = np.frombuffer(raw_audio, dtype=np.int16)
                audio_np = nr.reduce_noise(y=audio_np, sr=16000, prop_decrease=0.85)
                raw_audio = audio_np.astype(np.int16).tobytes()
        except ImportError:
            pass
        except Exception:
            pass
        self.eventos.put(sr.AudioData(raw_audio, 16000, 2))
        self.is_listening = False
        print("[NOVA] Procesando...")

    def _preparar_audio_vosk(self, audio_data):
        try:
            dtype = np.int16 if audio_data.sample_width == 2 else np.int8
            raw = np.frombuffer(audio_data.get_raw_data(), dtype=dtype)
            if audio_data.sample_rate != 16000:
                ratio = 16000 / audio_data.sample_rate
                new_len = int(len(raw) * ratio)
                if new_len > 0:
                    indices = np.linspace(0, len(raw) - 1, new_len)
                    raw = np.interp(indices, np.arange(len(raw)), raw)
            raw = np.clip(raw, -32768, 32767).astype(np.int16)
            return raw.tobytes()
        except Exception as e:
            print(f"[NOVA] Preparar audio error: {e}")
            return None

    def reconocer_audio(self, audio_data):
        texto = None
        if self.vosk_model:
            try:
                wav_16k = self._preparar_audio_vosk(audio_data)
                if wav_16k:
                    rec = KaldiRecognizer(self.vosk_model, 16000)
                    rec.AcceptWaveform(wav_16k)
                    res = json.loads(rec.Result())
                    texto = res.get("text", "").lower().strip()
                    if texto:
                        return texto
            except Exception as e:
                print(f"[NOVA] Vosk error: {e}")
        if not VOSK_AVAILABLE or not self.vosk_model:
            try:
                texto = self.recognizer.recognize_google(audio_data, language="es-CO").lower().strip()
                return texto
            except sr.UnknownValueError:
                pass
            except sr.RequestError:
                print("[NOVA] Sin internet — Google STT no disponible")
            except Exception as e:
                print(f"[NOVA] Google STT error: {e}")
        return texto

    def necesita_vision(self, prompt):
        t = prompt.lower()
        palabras = [
            "ves", "veo", "pantalla", "imagen", "captura", "screenshot",
            "muestra", "qué hay", "describe", "qué es esto", "qué es eso",
            "mira", "observa", "examina", "analiza", "revisa",
            "mi pantalla", "mi escritorio", "la pantalla", "la imagen"
        ]
        return any(p in t for p in palabras)

    def procesar_cola(self):
        if self.closing:
            return
        try:
            while not self.eventos.empty():
                item = self.eventos.get_nowait()
                if isinstance(item, sr.AudioData):
                    texto = self.reconocer_audio(item)
                    if texto:
                        self.procesar_texto(texto)
                    else:
                        print("[NOVA] No te escuché. Habla más cerca.")
                elif isinstance(item, str):
                    self.procesar_texto(item)
        except Exception as e:
            print(f"[NOVA] Error cola: {e}")
        finally:
            if not self.closing:
                self.after(120, self.procesar_cola)

        if self.nova3d:
            try:
                msg = self.nova3d.poll_chat()
                if msg == '__SHUTDOWN__':
                    print("[NOVA] Señal de cierre total recibida del modelo 3D")
                    self.on_close()
                    return
                if msg:
                    self.procesar_texto(msg)
            except Exception:
                pass

    def limpiar_texto(self, texto):
        return re.sub(r"\s+", " ", texto.strip())

    def procesar_texto(self, prompt):
        prompt = self.limpiar_texto(prompt or "")
        if not prompt or self.is_processing:
            return

        print(f"Tú: {prompt}")

        try:
            self._extraer_datos_perfil(prompt)
        except Exception as e:
            print(f"[NOVA] Error extrayendo perfil: {e}")

        if prompt.lower().startswith("recuerda que "):
            hecho = prompt[13:].strip()
            if hecho:
                self.mente.recordar(hecho)
                self.mente.actualizar_perfil_usuario("notas_libres", hecho)
                if self.nova3d:
                    try:
                        self.nova3d.state('celebrating')
                    except:
                        pass
                self.hablar(f"Apuntado. Recordaré que {hecho}")
            return

        if "mi nombre es " in prompt.lower():
            try:
                nombre = prompt.lower().split("mi nombre es ")[-1].strip().split()[0]
                nombre_cap = nombre.capitalize()
                self.mente.memoria["user_name"] = nombre_cap
                self.mente.guardar_memoria()
                self.mente.actualizar_perfil_usuario("nombre_preferido", nombre_cap)
                if self.nova3d:
                    try:
                        self.nova3d.expression('happy', 1.0)
                    except:
                        pass
                self.hablar(f"Encantada, {nombre_cap}. Ya lo tengo guardado.")
            except Exception as e:
                print(f"[NOVA] Error guardando nombre: {e}")
                self.hablar("Ups, no pude guardar tu nombre.")
            return

        try:
            if self.ejecutar_comandos(prompt):
                return
        except Exception as e:
            print(f"[NOVA] Error comando: {e}")

        if not self.ollama_available:
            self.hablar("Ollama no está disponible.")
            return

        if self.nova3d:
            try:
                self.nova3d.state('thinking')
            except:
                pass

        try:
            threading.Thread(target=self.responder_llm, args=(prompt,), daemon=True).start()
        except Exception as e:
            print(f"[NOVA] Error thread LLM: {e}")
            self.is_processing = False

    def _extraer_datos_perfil(self, texto):
        t = texto.lower()
        if any(x in t for x in ["me gusta", "me encanta", "amo"]) and any(x in t for x in ["música", "canción", "banda", "artista", "rock", "pop", "rap", "jazz", "clásica"]):
            for pref in ["me gusta", "me encanta", "amo"]:
                if pref in t:
                    parte = texto.lower().split(pref, 1)[-1].strip().split(".")[0]
                    self.mente.actualizar_perfil_usuario("preferencias.musica", parte)
                    break
        if any(x in t for x in ["me gusta comer", "mi comida favorita", "amo la comida"]):
            for pref in ["me gusta comer", "mi comida favorita es", "amo"]:
                if pref in t:
                    parte = texto.lower().split(pref, 1)[-1].strip().split(".")[0]
                    self.mente.actualizar_perfil_usuario("preferencias.comida", parte)
                    break
        if "te llamo" in t or "te digo" in t:
            for pref in ["te llamo", "te digo"]:
                if pref in t:
                    parte = texto.lower().split(pref, 1)[-1].strip().split()[0]
                    if parte and parte not in ["nova", "no"]:
                        self.mente.actualizar_perfil_usuario("relacion_con_nova.como_me_llama", parte)
                    break
        if any(x in t for x in ["quiero ser", "mi meta es", "mi sueño es", "aspiró a"]):
            for pref in ["quiero ser", "mi meta es", "mi sueño es", "aspiró a"]:
                if pref in t:
                    parte = texto.lower().split(pref, 1)[-1].strip().split(".")[0]
                    self.mente.actualizar_perfil_usuario("metas_y_sueños", parte)
                    break

    def ejecutar_comandos(self, texto):
        t = texto.lower().strip()
        if "sube el volumen" in t:
            for _ in range(5):
                pyautogui.press("volumeup")
            self.hablar("Hecho.")
            return True
        if "baja el volumen" in t:
            for _ in range(5):
                pyautogui.press("volumedown")
            self.hablar("Hecho.")
            return True
        if "silencio" in t or "mute" in t:
            pyautogui.press("volumemute")
            self.hablar("Hecho.")
            return True
        if "abre youtube" in t:
            webbrowser.open("https://youtube.com")
            self.hablar("Abriendo.")
            return True
        if "abre google" in t:
            webbrowser.open("https://google.com")
            self.hablar("Abriendo.")
            return True
        if "busca en google" in t:
            query = t.split("busca en google", 1)[-1].strip()
            if query:
                webbrowser.open(f"https://www.google.com/search?q={query}")
                self.hablar("Buscando.")
                return True
        if "busca en youtube" in t:
            query = t.split("busca en youtube", 1)[-1].strip()
            if query:
                webbrowser.open(f"https://www.youtube.com/results?search_query={query}")
                self.hablar("Buscando.")
                return True
        if "bloc de notas" in t:
            subprocess.Popen(["notepad.exe"])
            self.hablar("Abriendo.")
            return True
        if "calculadora" in t:
            subprocess.Popen(["calc.exe"])
            self.hablar("Abriendo.")
            return True
        if "administrador de tareas" in t:
            pyautogui.hotkey("ctrl", "shift", "esc")
            self.hablar("Abriendo.")
            return True
        if "captura" in t or "pantallazo" in t:
            pyautogui.hotkey("win", "prtscr")
            self.hablar("Captura lista.")
            return True
        if t.startswith("escribe "):
            contenido = texto[8:].strip()
            if contenido:
                pyautogui.write(contenido, interval=0.03)
                self.hablar("Escrito.")
                return True
        if any(x in t for x in ["salir", "cerrar", "adiós nova", "adios nova"]):
            self.hablar("Adiós.")
            self.after(1000, self.on_close)
            return True
        return False

    def tomar_captura_pantalla(self):
        try:
            screenshot = ImageGrab.grab()
            max_size = 512
            w, h = screenshot.size
            if w > max_size or h > max_size:
                ratio = min(max_size / w, max_size / h)
                new_w, new_h = int(w * ratio), int(h * ratio)
                screenshot = screenshot.resize((new_w, new_h), Image.Resampling.LANCZOS)
            temp_dir = tempfile.gettempdir()
            img_path = os.path.join(temp_dir, "nova_vision.jpg")
            screenshot.convert("RGB").save(img_path, format="JPEG", quality=60)
            return img_path
        except Exception as e:
            print(f"[NOVA] Error captura: {e}")
            return None

    def responder_llm(self, prompt):
        if self.is_processing:
            return
        self.is_processing = True
        try:
            self.mostrar_estado("pensando")
            print("[NOVA] Pensando...")
        except:
            pass

        try:
            self.historial[0] = {"role": "system", "content": self.mente.generar_contexto_sistema()}
        except Exception as e:
            print(f"[NOVA] Error contexto: {e}")

        usar_vision = False
        img_path = None
        try:
            usar_vision = self.necesita_vision(prompt)
        except:
            pass

        if usar_vision:
            try:
                print("[NOVA] Observando...")
                img_path = self.tomar_captura_pantalla()
            except Exception as e:
                print(f"[NOVA] Error captura: {e}")

        modelo = self.model_chat
        if usar_vision and img_path and os.path.exists(img_path):
            modelo = self.model_vision

        print(f"[NOVA] Modelo: {modelo}")

        try:
            if img_path and os.path.exists(img_path):
                mensaje_usuario = {"role": "user", "content": prompt, "images": [img_path]}
            else:
                mensaje_usuario = {"role": "user", "content": prompt}

            self.historial.append(mensaje_usuario)

            opciones = {
                "temperature": 0.5,
                "num_predict": 80,
                "num_ctx": 1024,
                "top_k": 20,
                "top_p": 0.75,
                "repeat_penalty": 1.1,
                "mirostat": 0,
            }

            res = ollama.chat(
                model=modelo,
                messages=self.historial,
                options=opciones,
                keep_alive=-1
            )
            msg = res["message"]["content"].strip()

            self.historial[-1] = {"role": "user", "content": prompt}
            self.historial.append({"role": "assistant", "content": msg})

            if len(self.historial) > 7:
                self.historial = [self.historial[0]] + self.historial[-6:]

            print(f"Nova: {msg[:100]}..." if len(msg) > 100 else f"Nova: {msg}")

            try:
                self.mente.registrar_interaccion(prompt, msg)
            except Exception as e:
                print(f"[NOVA] Error registro: {e}")

            if self.nova3d:
                try:
                    self.nova3d.state('speaking')
                except:
                    pass

            self.hablar(msg)

        except Exception as e:
            print(f"[NOVA] Error Ollama: {e}")
            if self.nova3d:
                try:
                    self.nova3d.state('error')
                except:
                    pass
            self.hablar("Error interno. Intenta de nuevo.")

        finally:
            self.is_processing = False
            try:
                self.mostrar_estado("idle")
                print("[NOVA] Listo. Mantén 5 para hablar." if self.mic_available else "[NOVA] Sin micrófono.")
            except:
                pass
            if img_path and os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except:
                    pass

    # ========== CIERRE INMEDIATO Y DEFINITIVO ==========
    def on_close(self):
        if self.closing:
            return
        self.closing = True
        print("[NOVA] Cerrando inmediatamente...")

        # 1. Detener todo audio TTS inmediatamente con fade-out suave
        self.is_speaking = False
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.fadeout(200)
                time.sleep(0.25)
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except:
            pass
        try:
            if SOUNDDEVICE_AVAILABLE:
                sd.stop()
        except:
            pass

        # 2. Detener grabación de micrófono
        self.ptt_active = False

        # 3. Cerrar modelo 3D
        if self.nova3d:
            try:
                self.nova3d.close()
            except Exception as e:
                print(f"[NOVA] Error cerrando 3D: {e}")

        # 4. Detener hotkeys
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except:
                pass

        # 5. Cerrar audio
        if self.audio_pyaudio:
            try:
                self.audio_pyaudio.terminate()
            except:
                pass

        # 6. Destruir ventana Tk
        try:
            self.destroy()
        except:
            pass

        # 7. Salir del programa completamente
        print("[NOVA] ✅ Cerrado.")
        os._exit(0)


if __name__ == "__main__":
    app = NovaApp()
    app.mainloop()