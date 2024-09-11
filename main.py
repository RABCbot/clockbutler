import os
import logging
import time
import json
import yaml
import asyncio
import aiohttp
import aiomqtt
from datetime import datetime
from gpiozero import Buzzer
from piper.voice import PiperVoice
import subprocess
import wave
import RPi.GPIO as GPIO


logging.basicConfig(
  format="%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s",
  level=os.getenv("LOGGING", logging.INFO),
  datefmt="%Y-%m-%d %H:%M:%S")

class Butler:
  def __init__(self, config_file):
    self.script_list = []
    self.favorite_dict = {}
    self.current_position = None
    self.mqtt_client = None
    self.config = self.read_config(config_file)

  def read_config(self, config_file):
    logging.info(f"Reading config")
    try:
      with open(config_file, "r") as f:
        return yaml.safe_load(f)

    except Exception as err:
      logging.critical(f"Failed reading config file {config_file}, because: str({err})")
      return None

  def set_scripts(self, names):
    try:
      self.script_list = json.loads(names)
      if len(self.script_list) > 0:
        self.current_position = 0
    except Exception as error:
      logging.error("Unable to parse script list")

  def inc_current(self):
    if self.current_position < len(self.script_list) - 1: 
      self.current_position = self.current_position + 1
    else:
      self.current_position = 0

  def dec_current(self):
    if self.current_position > 0: 
      self.current_position = self.current_position - 1
    else:
      self.current_position = len(self.script_list) - 1

  def get_current(self):
    if self.current_position is not None:
      return self.script_list[self.current_position]

  def set_favorite(self, index):
    if self.current_position: self.favorite_dict[index] = self.current_position

  def get_favorite(self, index):
    if index in self.favorite_dict.keys():
      pos = self.favorite_dict[index]
      return self.script_list[pos]

  async def gpi_listener(self):
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(16, GPIO.IN)

    while True:
      if GPIO.input(16) == False:
        logging.info('Button pressed')
        filename = self.config["sounds_path"]
        filename = os.path.join(filename, "beep.wav")
        prefix = self.config["mqtt_prefix"]
        action = self.get_current()
        await self.mqtt_client.publish(f"{prefix}/action", payload="button pressed")
        await self.aplay(filename)

      await asyncio.sleep(0.2)

  async def mqtt_listener(self):
    host = self.config["mqtt_host"]
    prefix = self.config["mqtt_prefix"]
    interval = self.config["interval"]

    async with aiomqtt.Client(host) as client:
      self.mqtt_client = client
      await client.subscribe(f"{prefix}/#")

      while True:
        try:
          async for message in client.messages:
            payload = str(message.payload.decode())
            logging.info(f"Mqtt listener message received; topic: {message.topic.value}, payload: {payload}")
            if message.topic.matches(f"{prefix}/say"): await self.say(payload)
            if message.topic.matches(f"{prefix}/volume"): await self.volume(payload)
            if message.topic.matches(f"{prefix}/play"): await self.play(payload)
            if message.topic.matches(f"{prefix}/scripts"): self.set_scripts(payload)

        except aiomqtt.MqttError as error:
          logging.info(f"Mqtt listener reconnecting...")
          await asyncio.sleep(interval)

    logging.info(f"Mqtt listener ended")
    return

  async def clock_worker(self):
    interval = self.config["interval"]

    while True:
      now = datetime.now()
      logging.info(f"{now:%a, %b %d %H:%M %p}")
      await asyncio.sleep(interval)

  async def volume(self, level):
    try:
      logging.info(f"Setting volume...")
      process = await asyncio.create_subprocess_exec(
        "amixer", "-c 0", "set", "Master", f"{level}%",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
      )
      stdout, stderr = await process.communicate()
      if process.returncode == 0:
        logging.info(f"Set volume succesfully")
        
        filename = self.config["sounds_path"]
        filename = os.path.join(path, "beep.wav")
        await aplay(filename)
      else:
        logging.error(f"Set volume error: {stderr.decode()}")
    except Exception as error:
      logging.error(f"Set volume exception: {error}")

  async def play(self, sound):
    try:
      filename = self.config["sounds_path"]
      filename = os.path.join(filename, f"{sound}.wav")
      await self.aplay(filename)

    except Exception as error:
      logging.error(f"Play exception: {error}")

  async def say(self, speech):
    try:
      model = self.config["model"]
      filename = self.config["speech_path"]
      filename = os.path.join(filename, f"{speech}.wav")

      if not os.path.isfile(filename):
        logging.info(f"Synthesizing speech...")
        voice = PiperVoice.load(model)
        wf = wave.open(filename, "wb")
        audio = voice.synthesize(speech, wf)

      await self.aplay(filename)

    except Exception as error:
      logging.error(f"Speech exception: {error}")

  async def aplay(self, filename):
    try:
      device = self.config["device"]
      logging.info(f"Aplaying speech...")
      process = await asyncio.create_subprocess_exec(
        'aplay', filename, f"--device={device}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
      )
      stdout, stderr = await process.communicate()
      if process.returncode == 0:
        logging.info(f"Aplay succesfully")
      else:
        logging.error(f"Aplay error: {stderr.decode()}")

    except Exception as error:
      logging.error(f"Aplay exception: {error}")

async def main():
  b = Butler(os.getenv("CONFIG_FILE"))
  async with asyncio.TaskGroup() as tg:
    logging.info(f"Creating mqtt listener")
    tg.create_task(b.mqtt_listener())
    logging.info(f"Creating gpi listener")
    tg.create_task(b.gpi_listener())
    logging.info(f"Creating clock worker")
    tg.create_task(b.clock_worker())
  logging.info(f"Main loop completed.")

if __name__ == "__main__":
  asyncio.run(main())
