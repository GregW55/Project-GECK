import asyncio
import discord
import time
from discord.ext import commands
from datetime import datetime
from kasa import Discover
from gpiozero import InputDevice, OutputDevice
import os
from dotenv import load_dotenv

"""
--- COMMANDS ---
!photo
!status
!light on/off
!auto
"""

# --- LOAD SECRETS ---
load_dotenv()

# --- CONFIGURATION ---
TOKEN = os.getenv('DISCORD_TOKEN')

# --- CHANNEL CONFIGURATION ---
CHANNEL_GENERAL_ID = int(os.getenv('CHANNEL_GENERAL'))
CHANNEL_EMERGENCY_ID = int(os.getenv('CHANNEL_EMERGENCY'))
CHANNEL_IMAGES_ID = int(os.getenv('CHANNEL_IMAGES'))

# --- HARDWARE CONFIGURATION ---
HUMITURE_PIN = 17

LIGHT_NAME = "Lights"
LIGHT_START = 8
LIGHT_END = 20

# --- SETUP BOT ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- GLOBAL VARIABLES ---
LIGHT_PLUG = None
OVERRIDE_LIGHT = False

LAST_PHOTO_PATH = None
LAST_PHOTO_TS = None

# How often to retry finding Kasa devices if missing/offline
DISCOVERY_RETRY_SECONDS = 11 * 60 * 60  # 11 hours
BROKEN_DISCOVERY_SECONDS = 30 * 60      # 30 minutes

# --- DISCOVERY FUNCTION ---
async def get_plugs_by_name():
    print("🔍 Scanning LAN for Kasa devices...")
    found_devices = await Discover.discover()

    light = None

    for ip, device in found_devices.items():
        try:
            await device.update()
            print(f"Found: {device.alias} at {ip}")
            if device.alias == LIGHT_NAME:
                light = device
        except Exception as e:
            print(f"Failed updating device at {ip}: {e}")
    return light

async def ensure_plugs_connected(force=False):
    """Ensure LIGHT_PLUG is set. Retry discovery occasionally."""
    global LIGHT_PLUG

    if force or LIGHT_PLUG is None:
        light = await get_plugs_by_name()
        LIGHT_PLUG = light or LIGHT_PLUG

    # Optional: sanity check by calling update() (won't crash automation)
    if LIGHT_PLUG:
        try:
            await LIGHT_PLUG.update()
        except Exception:
            pass


# --- SENSOR CLASS ---
class DHT11:
    MAX_DELAY_COUNT = 100
    BIT_1_DELAY_COUNT = 10
    BITS_LEN = 40

    def __init__(self, pin, pull_up=False):
        self._pin = pin
        self._pull_up = pull_up

    def read_data(self):
        bit_count = 0
        delay_count = 0
        bits = ""

        # Send Start Signal
        gpio = OutputDevice(self._pin)
        gpio.off()
        time.sleep(0.02)
        gpio.close()

        # Switch to Input
        gpio = InputDevice(self._pin, pull_up=self._pull_up)

        # Wait for Response (guard against infinite loop)
        start = time.time()
        while gpio.value == 1:
            if time.time() - start > 0.5:
                gpio.close()
                return None, None

        # Read Data
        while bit_count < self.BITS_LEN:
            start = time.time()
            while gpio.value == 0:
                if time.time() - start > 0.5:
                    gpio.close()
                    return None, None

            while gpio.value == 1:
                delay_count += 1
                if delay_count > self.MAX_DELAY_COUNT:
                    break
            if delay_count > self.BIT_1_DELAY_COUNT:
                bits += "1"
            else:
                bits += "0"

            delay_count = 0
            bit_count += 1

        gpio.close()
        # Process Bits
        try:
            humidity_integer = int(bits[0:8], 2)
            humidity_decimal = int(bits[8:16], 2)
            temperature_integer = int(bits[16:24], 2)
            temperature_decimal = int(bits[24:32], 2)
            check_sum = int(bits[32:40], 2)

            _sum = humidity_integer + humidity_decimal + temperature_integer + temperature_decimal

            if check_sum != _sum:
                return None, None

            humidity = float(f'{humidity_integer}.{humidity_decimal}')
            temperature_c = float(f'{temperature_integer}.{temperature_decimal}')
            temperature_f = temperature_c * (9 / 5) + 32
            return humidity, temperature_f

        except Exception:
            return None, None


# --- HELPER: CAMERA ---
async def take_photo_logic():
    global LAST_PHOTO_PATH, LAST_PHOTO_TS

    folder = "photos"
    if not os.path.exists(folder):
        os.makedirs(folder)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{folder}/grow_{timestamp}.jpg"

    # -o: Output filename
    # -t 5000: Wait 5 seconds for light adjustment (warmup)
    # --awbgains 1.3,1.9: Adjust color balance manually
    # --nopreview: Run headless
    # --quality 100: Best quality
    cmd = f"rpicam-still -o {filename} -t 5000 --awbgains 1.3,1.9 --nopreview --quality 100"

    try:
        process = await asyncio.create_subprocess_shell(cmd)
        rc = await process.wait()
        if rc != 0 or not os.path.exists(filename):
            raise RuntimeError(f"rpicam-still failed rc={rc}")

        LAST_PHOTO_PATH = filename
        LAST_PHOTO_TS = datetime.now()
        return filename
    except Exception as e:
        print(f"Photo failed: {e}")
        return None


# --- DISCORD SAFE SEND ---
async def discord_send(channel_id: int, content: str = None, file_path: str = None):
    """Send to Discord if connected, never raise."""
    if channel_id == 0:
        return
    if not bot.is_ready():
        return

    try:
        chan = bot.get_channel(channel_id)
        if chan is None:
            return
        if file_path:
            await chan.send(content or "", file=discord.File(file_path))
        else:
            if content:
                await chan.send(content)
    except Exception as e:
        # Don't let Discord failures kill automation
        print(f"Discord send failed: {e}")

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f"--- Discord connected as {bot.user} ---")
    # NOTE: we do NOT start automation here anymore.
    # We only use on_ready as an optional hook to refresh plugs immediately.
    await ensure_plugs_connected(force=False)


# --- COMMANDS ---
@bot.command()
async def auto(ctx):
    """Resumes the Schedule (Disables Manual Mode)"""
    global OVERRIDE_LIGHT
    OVERRIDE_LIGHT = False
    await ctx.send("**Automation Resumed.** Schedule is back in control.")


@bot.command()
async def status(ctx):
    dht = DHT11(HUMITURE_PIN)
    hum, temp = dht.read_data()

    # Safe formatting
    temp_s = "ERR" if temp is None else f"{temp:.1f}F"
    hum_s = "ERR" if hum is None else f"{hum:.1f}%"

    l_state = "Offline"
    l_mode = "**MANUAL**" if OVERRIDE_LIGHT else "**AUTO**"

    if LIGHT_PLUG:
        try:
            await LIGHT_PLUG.update()
            l_state = "ON" if LIGHT_PLUG.is_on else "OFF"
        except Exception:
            l_state = "Offline"

    msg = (f"**🌱 Garden Status**\n"
           f"🌡️ Temp: `{temp_s}`\n"
           f"💧 Humidity: `{hum_s}`\n"
           f"☀️ Light: `{l_state}` ({l_mode})\n"
    )
    await ctx.send(msg)


@bot.command()
async def photo(ctx):
    await ctx.send("📸 Snapping photo (wait 5s)...")
    filename = await take_photo_logic()
    if filename:
        await ctx.send(file=discord.File(filename))
    else:
        await ctx.send("Photo Failed.")


@bot.command()
async def light(ctx, state: str):
    global OVERRIDE_LIGHT
    if not LIGHT_PLUG:
        return await ctx.send("Light plug not connected.")

    OVERRIDE_LIGHT = True

    try:
        if state.lower() == "on":
            await LIGHT_PLUG.turn_on()
            await ctx.send("Light forced **ON** (Manual Mode Active)")
        elif state.lower() == "off":
            await LIGHT_PLUG.turn_off()
            await ctx.send("Light forced **OFF** (Manual Mode Active)")
        else:
            await ctx.send("Usage: '!light on' or '!light off'")
    except Exception as e:
        await ctx.send(f"Failed: {e}")

# --- AUTOMATION LOOP ---
async def automation_runner():
    """
    Runs forever, regardless of Discord connection.
    Discord is used only for optional notifications.
    """
    global OVERRIDE_LIGHT

    print("Automation runner started (Discord optional).")

    #last_discovery = 0.0
    next_discovery_ts = 0.0  # Discover immediately on boot
    last_hour_sent = -1

    while True:
        now = datetime.now()
        now_ts = time.monotonic()

        # Periodically retry discovery so Kasa can come/go without requiring Discord
        if now_ts >= next_discovery_ts:
            # If we don't have a plug, force a full scan
            await ensure_plugs_connected(force=(LIGHT_PLUG is None))

            # Decide the next interval based on whether we're "healthy"
            if LIGHT_PLUG is not None:
                next_discovery_ts = now_ts + DISCOVERY_RETRY_SECONDS   # 11 hours
            else:
                next_discovery_ts = now_ts + BROKEN_DISCOVERY_SECONDS  # 30 minutes


        # 1 Sensor
        dht = DHT11(HUMITURE_PIN)
        hum, temp = dht.read_data()

        # 2 Overheat safety (local action first)
        if temp is not None and temp > 80.0:
            # Try to kill lights locally
            if LIGHT_PLUG:
                try:
                    await LIGHT_PLUG.turn_off()
                except Exception:
                    pass
            # Optional Discord notification
            await discord_send(
                CHANNEL_EMERGENCY_ID,
                f"@everyone **OVERHEAT:** {temp:.1f}F! Killing Lights."
            )

        # 3 Light schedule (local control)
        elif LIGHT_PLUG and not OVERRIDE_LIGHT:
            try:
                await LIGHT_PLUG.update()
                if LIGHT_START <= now.hour < LIGHT_END:
                    if not LIGHT_PLUG.is_on:
                        await LIGHT_PLUG.turn_on()
                        print("Lights auto-ON")
                        await discord_send(CHANNEL_GENERAL_ID, "Lights Auto-ON")

                else:
                    if LIGHT_PLUG.is_on:
                        await LIGHT_PLUG.turn_off()
                        print("Lights Auto-OFF")
                        await discord_send(CHANNEL_GENERAL_ID, "Lights Auto-OFF")
            except Exception:
                pass

        # 4 Hourly photo (local action first, Discord optional)
        if now.minute == 0 and now.hour != last_hour_sent:
            filename = await take_photo_logic()
            if filename:
                await discord_send(
                    CHANNEL_IMAGES_ID,
                    f"📷 Hourly Update: {now.strftime('%I:%M %p')}",
                    file_path=filename
                )
            last_hour_sent = now.hour
        await asyncio.sleep(10)

# --- DISCORD RUNNER (RECONNECTS) ---
async def discord_runner():
    """
    Keeps trying to connect to Discord forever.
    If Discord is down at boot, automation still runs.
    """
    if not TOKEN:
        print("DISCORD_TOKEN missing, Discord bot will not run.")
        return
    
    while True:
        try:
            print("Starting Discord Client...")
            await bot.start(TOKEN)  # blocks until disconnected or error
        except Exception as e:
            print(f" Discord client error/disconnect: {e}")
            # Wait a bit, then retry
            await asyncio.sleep(15)
            
# --- MAIN ---
async def main():
    # Start automation first (always)
    automation_task = asyncio.create_task(automation_runner())
    # Start discord runner (optional)
    discord_task = asyncio.create_task(discord_runner())
    
    # Wait forever (if one task dies, print why and keep the other alive)
    done, pending = await asyncio.wait(
        {automation_task, discord_task},
        return_when=asyncio.FIRST_EXCEPTION
    )
    
    for task in done:
        try:
            task.result()
        except Exception as e:
            print(f"Task Crashed: {e}")
    
    # Keep remaining tasks alive
    await asyncio.gather(*pending)
    
    
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
