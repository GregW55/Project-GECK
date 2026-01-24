import asyncio
import discord
import time
from discord.ext import commands, tasks
from datetime import datetime
from kasa import SmartPlug, Discover
from gpiozero import InputDevice, OutputDevice
import os
from dotenv import load_dotenv
"""
--- COMMANDS ---
!photo
!status
!light on/off
!pump on/off
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
PUMP_NAME = "Pump plug"
LIGHT_START = 8
LIGHT_END = 20
PUMP_MINUTES = 15

# --- SETUP BOT ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- GLOBAL VARIABLES ---
PLUG_LIGHT = None
PLUG_PUMP = None

# --- OVERRIDE FLAGS ---
OVERRIDE_LIGHT = False
OVERRIDE_PUMP = False

# --- DISCOVERY FUNCTION
async def get_plugs_by_name():
    print("🔍 Scanning network for Kasa devices...")
    found_devices = await Discover.discover()
    
    light = None
    pump = None
    
    for ip, device in found_devices.items():
        await device.update()
        print(f"Found: {device.alias} at {ip}")
        if device.alias == LIGHT_NAME:
            light = device
        elif device.alias == PUMP_NAME:
            pump = device
    return light, pump

# --- SENSOR CLASS ---
class DHT11():
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

        # Wait for Response
        while gpio.value == 1:
            pass

        # Read Data
        while bit_count < self.BITS_LEN:
            while gpio.value == 0:
                pass

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

        # Process Bits
        try:
            humidity_integer = int(bits[0:8], 2)
            humidity_decimal = int(bits[8:16], 2)
            temperature_integer = int(bits[16:24], 2)
            temperature_decimal = int(bits[24:32], 2)
            check_sum = int(bits[32:40], 2)

            _sum = humidity_integer + humidity_decimal + temperature_integer + temperature_decimal

            if check_sum != _sum:
                return None, None  # Checksum failed
            else:
                humidity = float(f'{humidity_integer}.{humidity_decimal}')
                temperature = float(f'{temperature_integer}.{temperature_decimal}')
                # Convert to Fahrenheit
                temperature_f = temperature * (9 / 5) + 32
                return humidity, temperature_f
        except:
            return None, None


# --- HELPER: CAMERA ---
async def take_photo_logic():
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

    process = await asyncio.create_subprocess_shell(cmd)
    await process.wait()
    return filename


# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f'--- Logged in as {bot.user} ---')

    global PLUG_LIGHT, PLUG_PUMP
    if not PLUG_LIGHT or not PLUG_PUMP:
        PLUG_LIGHT, PLUG_PUMP = await get_plugs_by_name()

    if PLUG_LIGHT:
        print(f"✅ Light Connected: {PLUG_LIGHT.alias}")
    else:
        print("❌ Light NOT found")
    if PLUG_PUMP:
        print(f"✅ Pump Connected: {PLUG_PUMP.alias}")
    else:
        print("❌ Pump NOT found")

    if not automation_loop.is_running():
        automation_loop.start()


# --- COMMANDS ---
@bot.command()
async def auto(ctx):
    """Resumes the Schedule (Disables Manual Mode)"""
    global OVERRIDE_LIGHT, OVERRIDE_PUMP
    OVERRIDE_LIGHT = False
    OVERRIDE_PUMP = False
    await ctx.send("**Automation Resumed.** Schedule is back in control.")

@bot.command()
async def status(ctx):
    dht = DHT11(HUMITURE_PIN)
    hum, temp = dht.read_data()

    l_state = "Offline"
    p_state = "Offline"
    l_mode = "Wait..."
    p_mode = "Wait..."

    if PLUG_LIGHT:
        try:
            await PLUG_LIGHT.update(); l_state = "ON" if PLUG_LIGHT.is_on else "OFF"
        except:
            pass
        l_mode = "**MANUAL**" if OVERRIDE_LIGHT else "**AUTO**"

    if PLUG_PUMP:
        try:
            await PLUG_PUMP.update(); p_state = "ON" if PLUG_PUMP.is_on else "OFF"
        except:
            pass
        p_mode = "**MANUAL**" if OVERRIDE_PUMP else "**AUTO**"

    msg = (f"**🌱 Garden Status**\n"
           f"🌡️ Temp: `{temp:.1f}F`\n"
           f"💧 Humidity: `{hum:.1f}%`\n"
           f"☀️ Light: `{l_state}` ({l_mode})\n"
           f"🌊 Pump: `{p_state}` ({p_mode})")
    await ctx.send(msg)


@bot.command()
async def photo(ctx):
    await ctx.send("📸 Snapping photo (wait 5s)...")
    filename = await take_photo_logic()
    await ctx.send(file=discord.File(filename))


@bot.command()
async def light(ctx, state: str):
    global OVERRIDE_LIGHT
    if not PLUG_LIGHT: return await ctx.send("Light plug not connected.")

    # Enable Manual Mode
    OVERRIDE_LIGHT = True

    if state.lower() == "on":
        await PLUG_LIGHT.turn_on()
        await ctx.send("Light forced **ON** (Manual Mode Active)")
    elif state.lower() == "off":
        await PLUG_LIGHT.turn_off()
        await ctx.send("Light forced **OFF** (Manual Mode Active)")


@bot.command()
async def pump(ctx, state: str):
    global OVERRIDE_PUMP
    if not PLUG_PUMP: return await ctx.send("Pump plug not connected.")

    # Enable Manual Mode
    OVERRIDE_PUMP = True

    if state.lower() == "on":
        await PLUG_PUMP.turn_on()
        await ctx.send("Pump forced **ON** (Manual Mode Active)")
    elif state.lower() == "off":
        await PLUG_PUMP.turn_off()
        await ctx.send("Pump forced **OFF** (Manual Mode Active)")


# --- AUTOMATION LOOP ---
@tasks.loop(seconds=10)
async def automation_loop():
    now = datetime.now()
    chan_gen = bot.get_channel(CHANNEL_GENERAL_ID)
    chan_emg = bot.get_channel(CHANNEL_EMERGENCY_ID)
    chan_img = bot.get_channel(CHANNEL_IMAGES_ID)

    # 1. Sensor
    dht = DHT11(HUMITURE_PIN)
    hum, temp = dht.read_data()

    # 2. Overheat Check -> EMERGENCY CHANNEL
    if temp and temp > 90.0:
        if chan_emg: await chan_emg.send(f"@everyone **OVERHEAT:** {temp:.1f}F! Killing Lights.")
        if PLUG_LIGHT:
            try:
                await PLUG_LIGHT.turn_off()
            except:
                pass

    # 3. Light Schedule -> GENERAL CHANNEL
    elif PLUG_LIGHT and not OVERRIDE_LIGHT:
        try:
            await PLUG_LIGHT.update()
            if LIGHT_START <= now.hour < LIGHT_END:
                if not PLUG_LIGHT.is_on:
                    await PLUG_LIGHT.turn_on()
                    if chan_gen: await chan_gen.send("Lights Auto-ON")
                    print("Lights auto-ON")
            else:
                if PLUG_LIGHT.is_on:
                    await PLUG_LIGHT.turn_off()
                    if chan_gen: await chan_gen.send("Lights Auto-OFF")
                    print("Lights Auto-OFF")
        except:
            pass

    # 4. Pump Schedule (Silent unless error)
    if PLUG_PUMP and not OVERRIDE_PUMP:
        try:
            await PLUG_PUMP.update()
            if now.minute < PUMP_MINUTES:
                if not PLUG_PUMP.is_on:
                    await PLUG_PUMP.turn_on()
                    if chan_gen:
                        await chan_gen.send("Pump Auto-ON")
                        print("Pump Auto-ON")
            else:
                if PLUG_PUMP.is_on:
                    await PLUG_PUMP.turn_off()
                    if chan_gen:
                        await chan_gen.send("Pump Auto-OFF")
                        print("Pump Auto-OFF")
        except: pass

    # 5. Hourly Photo -> IMAGES CHANNEL
    if not hasattr(automation_loop, "last_hour"): automation_loop.last_hour = -1

    if now.minute == 0 and now.hour != automation_loop.last_hour:
        filename = await take_photo_logic()
        if chan_img:
            await chan_img.send(f"📷 Hourly Update: {now.strftime('%I:%M %p')}", file=discord.File(filename))
        automation_loop.last_hour = now.hour


# --- RUN ---
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("Bot stopped.")
