#!/usr/bin/env python3
"""
Power button -> screen on/off, exactly as in the Withrow build guide.

GPIO 26 (BCM) is the front power button, pulled up. GPIO 18 drives the
Waveshare backlight, GPIO 19 carries PWM audio (switched to input when
the screen is off so the speaker goes quiet too).
"""
import subprocess
import time

import RPi.GPIO as GPIO

BUTTON = 26
BACKLIGHT = 18
AUDIO = 19

# If the button does the opposite of what you want (screen on when it
# should be off), set this to True.
INVERT = False


def gpio(*args):
    subprocess.call(["raspi-gpio", "set"] + [str(a) for a in args])


def screen_on():
    gpio(AUDIO, "op", "a5")
    GPIO.output(BACKLIGHT, GPIO.HIGH)


def screen_off():
    gpio(AUDIO, "ip")
    GPIO.output(BACKLIGHT, GPIO.LOW)


def main():
    gpio(AUDIO, "ip")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BACKLIGHT, GPIO.OUT)

    screen_off()
    state = False
    try:
        while True:
            pressed = bool(GPIO.input(BUTTON))
            if INVERT:
                pressed = not pressed
            if pressed != state:
                state = pressed
                if state:
                    screen_on()
                else:
                    screen_off()
            time.sleep(0.3)
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
