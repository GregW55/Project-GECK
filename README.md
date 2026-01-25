# Project-GECK ☢️🍓
**Garden of Eden Creation Kit**

> "Bringing life to the wasteland... or just growing strawberries in my room."

## 📖 Overview
Project-GECK is a Python-based automation controller for a Raspberry Pi 5 hydroponics system. It acts as the central brain for an **Ebb & Flow** setup, managing light cycles, water distribution, and climate safety.

The system is fully integrated with **Discord**, allowing for remote monitoring via live camera feeds, status checks, and manual hardware overrides from anywhere.

## ⚡ Features
* **Automated Life Support:** Handles daily light cycles (12/12) and precise flood-and-drain pump schedules.
* **Environmental Safety:** Monitors Temperature & Humidity via DHT11. Automatically triggers an **Emergency Cutoff** for lights if temps exceed the threshold.
* **Remote Vision:** Captures and uploads hourly high-res images of plant growth to a dedicated Discord channel.
* **Manual Override System:** Discord commands (`!light off`, `!pump on`) instantly pause the automation schedule to allow for maintenance, with a `!auto` command to resume the logic.

## 🛠️ Hardware Requirements
* **Controller:** Raspberry Pi 5
* **Sensors:** DHT11 Temperature & Humidity Sensor (GPIO 17)
* **Camera:** Raspberry Pi Camera Module 3 (or compatible)
* **Power Control:** TP-Link Kasa Smart Plugs (WiFi)
* **Hydroponics:** 12V Submersible Pump & Grow Lights
* **Optional Display:** I2C LCD1602 (PCF8574 backpack)

## 💻 Tech Stack
* **Python 3.11+**
* **discord.py** (Bot Interface)
* **python-kasa** (Smart Plug Control)
* **gpiozero** (Sensor Data)

## 🤖 Discord Commands
| Command | Description |
| :--- | :--- |
| `!status` | Returns current Temp, Humidity, and Hardware State (Auto/Manual). |
| `!photo` | Snaps a realtime photo and uploads it to chat. |
| `!light [on/off]` | Force enables/disables grow lights and **pauses** the schedule. |
| `!pump [on/off]` | Force enables/disables water pump and **pauses** the schedule. |
| `!auto` | Disables manual overrides and resumes the automated schedule. |

## ⚙️ Installation & Setup
1.  **Clone the Repository**
    ```bash
    git clone https://github.com/GregW55/Project-GECK.git
    cd Project-GECK
    ```

2.  **Install Dependencies**
    ```bash
    pip install discord.py python-kasa gpiozero rpi-lgpio smbus2
    ```

3.  **Configuration**
    * Open `main.py` and insert your **Discord Bot Token**.
    * Update the `CHANNEL_ID` variables with your Discord Channel IDs.
    * Ensure your Kasa Plugs are named exactly `"Lights"` and `"Pump plug"` in the Kasa App.
    * (Optional) Enable the LCD1602 output:
      * `LCD_ENABLED=1`
      * `LCD_I2C_ADDR=0x27` (or `0x3f`)
      * `LCD_BACKLIGHT=1`
      * `LCD_CYCLE_SECONDS=5`

4.  **Run the GECK**
    ```bash
    python main.py
    ```

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
