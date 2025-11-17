import lauterbach.trace32.rcl as t32
import keyboard
import os
import subprocess
import time
import re
import asyncio
from tqdm import tqdm

TIMEOUT_TRESHOLD = 20

T32PPCPath = r"C:\T32N\bin\windows64\t32mppc.exe"
T32ConfigFile = r"C:\T32N\config.t32"

SCRIPTS = {
    "dps": 'do C:\\Git\\ibcu\\Tools\\Lauterbach\\T32SingleWinConfig\\spc58nn_setup_DPS.cmm',
    "calib": 'do C:\\Git\\ibcu\\Tools\\Lauterbach\\T32SingleWinConfig\\spc58nn_setup_Calib.cmm',
    "flashing_window": 'do C:\\T32N\\bin\\windows64\\FlashingDaRitardati.cmm'
}

# Todo of next features:
# Add statistics of a function:
# Example: Given a function address (for example, RIOManSlow address), get the timestamp of that record ()

class T32TestHandler:
    def __init__(self):
        self.is_system_powered = False
        self.was_system_powered = False
        self.is_flashed = False
        self.is_program_running = False
        self.routine_checks_done = False
        self.dbg = None

    async def openloop_test(self):
        self.dbg.variable.write("c_BCSW_OpenloopTest", 1)
        self.dbg.variable.write("c_BCSW_EnFaultRcvyReq", 1)
        time.sleep(0.01)
        self.dbg.variable.write("c_BCSW_EnFaultRcvyReq", 0)
        pressed_time = 0
        while keyboard.is_pressed('o'):
            pressed_time += 1
            self.dbg.variable.write("CAL_SRV_OpenLoopPWM", pressed_time * 10)
            await asyncio.sleep(0.3)
        self.dbg.variable.write("CAL_SRV_OpenLoopPWM", 0)

    async def check_program_running(self):
        state = self.dbg.fnc.state_target()
        print(state)
        if state == 'stopped' and self.is_system_powered:
            print("Program stopped! Currently executed functions:")
            self.is_program_running = False
            for i in range(3):
                self.dbg.cmd('CORE.Select ' + str(i))
                break_address = self.dbg.address.from_string(self.dbg.fnc.pp())
                print(f"Core {i}: {self.dbg.fnc.symbol_name(break_address)} || address: {break_address}")
            self.dbg.cmd('CORE.Select 0')
        elif state == 'running':
            self.is_program_running = True
            await self.routine_checks()
        await asyncio.sleep(2)


    async def routine_checks(self):
        # Read the status register bytes
        status_byte1 = self.dbg.variable.read_by_name("BLDCPD_Register_STATUS[0]")
        status_byte2 = self.dbg.variable.read_by_name("BLDCPD_Register_STATUS[1]")

        # Check for normal operation
        if status_byte1 == 0 and status_byte2 == 1:
            print("\033[92mECU PASSED ALL TESTS. EXITING\033[0m")
            self.routine_checks_done = True
            return

        # Define fault messages based on bits from page 52 of the datasheet
        fault_messages_byte1 = {
            0: "Status Register Flag",
            1: "Power-on Reset",
            2: "Serial Error",
            3: "EEPROM Fault",
            4: "Overtemperature",
            5: "High temperature warning",
            6: "VBB out of range",
            7: "Logic overvoltage"
        }

        fault_messages_byte2 = {
            0: "Logic overvoltage",
            1: "Watchdog error",
            2: "Regulator Voltage out of range",
            3: "VIO or VOOR out of range",
            4: "Load Fault",
            5: "Bootstrap undervoltage",
            6: "VGS undervoltage",
            7: "VDS overvoltage"
        }

        # Check each bit in Byte1
        for bit in range(8):
            if status_byte1 & (1 << bit):
                print(f"\033[91mFault Detected: {fault_messages_byte1.get(bit, f'Unknown fault in Byte1 bit {bit}')}\033[0m")

        # Check each bit in Byte2
        for bit in range(8):
            if status_byte2 & (1 << bit) and not (bit == 1 and status_byte1 == 0 and status_byte2 == 1):
                print(f"\033[91mFault Detected: {fault_messages_byte2.get(bit, f'Unknown fault in Byte2 bit {bit}')}\033[0m")


    async def poll_power_state(self):
        self.was_system_powered = self.is_system_powered
        self.is_system_powered = self.dbg.fnc.state_power()
        if self.is_system_powered and not self.was_system_powered:
            print("[INFO] System just powered ON. Awaiting flashing.")
            self.is_flashed = False
        print("System is ON!" if self.is_system_powered else "System is OFF!")
        await asyncio.sleep(10)

    async def idle_performance(self):
        
        return 


    async def func_performance(self, funcName):

        return

    async def run_script(self, script):
        self.dbg.cmd('InterCom.En Karim')
        self.dbg.cmd(script)
        scriptTimeOut = 0
        pbar = tqdm(total=TIMEOUT_TRESHOLD, desc="Flashing in progress", unit="s")
        while scriptTimeOut < TIMEOUT_TRESHOLD:
            practice_script_state = self.dbg.fnc.intercom_getpracticestate('Karim')
            pbar.update(1)
            if practice_script_state == "running":
                time.sleep(0.5)
                scriptTimeOut += 1
                continue
            if re.compile(r"err_[a-z]{,}").match(practice_script_state):
                print("\033[91mFlashing failed. Unlucky. Exiting")
                print("Error: " + practice_script_state + "\033[0m")
                self.dbg.cmd('ERROR.RESet')
                self.is_flashed = False
                return
            if practice_script_state == "stopped":
                if self.dbg.fnc.error_occurred():
                    print(self.dbg.fnc.error_message())
                    print("\033[91mFlashing failed. Unlucky. Exiting\033[0m")
                    self.dbg.cmd('ERROR.RESet')
                    self.is_flashed = False
                    return
                else:
                    print("\033[92mFlashed ECU successfully. Exiting\033[0m")
                    pbar.update(TIMEOUT_TRESHOLD - scriptTimeOut)
                    pbar.close()
                    self.is_flashed = True
                    return

    async def handle_user_input(self):
        while True:
            if keyboard.is_pressed('d'):
                print("[INFO] 'D' key detected. Flashing DPS...")
                await self.run_script(SCRIPTS['dps'])
            elif keyboard.is_pressed('c'):
                print("[INFO] 'C' key detected. Flashing Calibration...")
                await self.run_script(SCRIPTS['calib'])
            elif keyboard.is_pressed('o'):
                await self.openloop_test()
            elif keyboard.is_pressed('esc'):
                print("[INFO] Exiting...")
                os._exit(0)
            await asyncio.sleep(0.1)

    async def main_loop(self):
        self.dbg = t32.connect(node='localhost', port=20000, protocol="TCP", timeout=10.0)
        self.dbg.cmd(SCRIPTS["flashing_window"])
        print("Press 'D' to flash DPS script.")
        print("Press 'C' to flash Calibration script.")
        print("Press 'S' to Check CPU status (Det_ReportError has been hit)")
        print("Press 'Esc' to exit.")

        asyncio.create_task(self.handle_user_input())

        while True:
            await self.poll_power_state()
            if self.is_flashed and self.is_system_powered and not self.routine_checks_done:
                await self.check_program_running()
            elif self.is_flashed and not self.is_system_powered:
                self.is_flashed = False

if __name__ == "__main__":
    handler = T32TestHandler()
    asyncio.run(handler.main_loop())