import lauterbach.trace32.rcl as t32
import keyboard
import os
import subprocess
import traceback
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


TRACE32_CANDIDATES = {
    "t32mppc.exe",
    "t32start.exe",
    "t32.exe",
    "t32marm.exe",
    "t32mpower.exe",  # add others you use
}

def any_trace32_running() -> bool:
    """
    Returns True if a process with the same image name as one of the instances of t32 is running.
    Works without external packages by using Windows 'tasklist'.
    """
    try:
        out = subprocess.check_output(["tasklist"], stderr=subprocess.STDOUT, text=True)
        lower_out = out.lower()
        return any(name in lower_out for name in (n.lower() for n in TRACE32_CANDIDATES))
    except subprocess.CalledProcessError:
        return False
    
def start_trace32_if_needed(exe_path: str, config_path: str) -> bool:
    """
    Starts TRACE32 only if not already running.
    Returns True if started, False if already running or failed.
    """
    if any_trace32_running():
        print("[INFO] TRACE32 already running — not starting a new instance.")
        return False
    try:
        subprocess.Popen([exe_path, config_path], shell=False)
        print("[INFO] TRACE32 started.")
        return True
    except FileNotFoundError:
        print(f"[ERROR] Executable not found: {exe_path}")
    except Exception as e:
        print(f"[ERROR] Failed to start TRACE32: {e}")
    return False


class T32TestHandler:
    def __init__(self):
        self.is_system_powered = False
        self.was_system_powered = False
        self.is_flashed = False
        self.is_program_running = False
        self.routine_checks_done = False
        self.dbg = None
        self.idle_performance_done = False
        self.flashing = False
        self.has_stopped = False
        self.previous_state = "unknown"
        self.current_state = "unknown"
        self.predriver_is_faulty = False

        start_trace32_if_needed(T32PPCPath, T32ConfigFile)

        self.dbg = t32.connect(node='localhost', port=20000, protocol="TCP", timeout=40.0)

        self.dbg.cmd(SCRIPTS["flashing_window"])
        print("Press 'D' to flash DPS script.")
        print("Press 'C' to flash Calibration script.")
        print("Press 'S' to Check CPU status (Det_ReportError has been hit)")
        print("Press 'Esc' to exit.")

    def _sanitize_cmm_cmd(self, script_or_cmd: str) -> str:
        """
        Accepts strings like 'do C:\\path\\script.cmm' and returns 'C:\\path\\script.cmm'.
        If already a bare path, returns unchanged.
        """
        s = script_or_cmd.strip()
        if s.lower().startswith('do '):
            s = s[3:].strip()
        return s



    async def openloop_test(self):
        # Check preconditions before starting
        if not self.is_system_powered:
            print("\033[93m[WARN] System power is OFF. Cannot run open-loop test.\033[0m")
            return
        if not self.is_flashed:
            print("\033[93m[WARN] ECU not flashed. Cannot run open-loop test.\033[0m")
            return
        if not self.is_program_running:
            print("\033[93m[WARN] Program is not running. Cannot run open-loop test.\033[0m")
            return

        # Double-check TRACE32 target state
        try:
            state = self.dbg.fnc.state_target()
            if state != "running":
                print(f"\033[93m[WARN] Target state is '{state}'. Must be RUNNING.\033[0m")
                return
        except Exception as e:
            print(f"[WARN] Could not read target state: {e}")
            return

        # Try writing initial variables safely
        try:
            self.dbg.variable.write("c_BCSW_OpenloopTest", 1)
            self.dbg.variable.write("c_BCSW_EnFaultRcvyReq", 1)
            await asyncio.sleep(0.01)
            self.dbg.variable.write("c_BCSW_EnFaultRcvyReq", 0)
        except CommandError as e:
            print(f"\033[91m[ERROR] Failed to initialize open-loop test: {e}\033[0m")
            return

        # PWM control loop
        pressed_time = 0
        while keyboard.is_pressed('o'):
            # If system goes down mid-loop, stop gracefully
            if not (self.is_system_powered and self.is_program_running):
                print("\033[93m[WARN] System no longer ready. Stopping actuation.\033[0m")
                break

            pressed_time += 1
            try:
                self.dbg.variable.write("CAL_SRV_OpenLoopPWM", pressed_time * 10)
            except CommandError as e:
                print(f"\033[91m[ERROR] Failed to write PWM: {e}\033[0m")
                break

            await asyncio.sleep(0.3)

        # Reset PWM at the end
        try:
            self.dbg.variable.write("CAL_SRV_OpenLoopPWM", 0)
        except CommandError as e:
            print(f"\033[93m[WARN] Could not reset PWM: {e}\033[0m")


    async def check_program_running(self):
        self.previous_state = self.current_state
        self.current_state = self.dbg.fnc.state_target()
        if self.current_state != self.previous_state:
            print(self.current_state)
        if self.current_state == 'stopped' and self.is_system_powered and not self.has_stopped:
            self.has_stopped = True
            print("Program stopped! Currently executed functions:")
            self.is_program_running = False
            for i in range(3):
                self.dbg.cmd('CORE.Select ' + str(i))
                break_address = self.dbg.address.from_string(self.dbg.fnc.pp())
                print(f"Core {i}: {self.dbg.fnc.symbol_name(break_address)} || address: {break_address}")
            self.dbg.cmd('CORE.Select 0')
        elif self.current_state == 'running':
            self.is_program_running = True
            self.has_stopped = False
            await self.routine_checks()
        await asyncio.sleep(2)


    async def routine_checks(self):
        # Read the status register bytes
        status_byte1 = self.dbg.variable.read_by_name("BLDCPD_Register_STATUS[0]").value
        status_byte2 = self.dbg.variable.read_by_name("BLDCPD_Register_STATUS[1]").value

        # Check for normal operation
        if status_byte1 == 0 and status_byte2 == 1:
            print("\033[92mECU PASSED ALL TESTS. EXITING\033[0m")
            self.routine_checks_done = True
            self.predriver_is_faulty = False
            return
        else:
            if (self.predriver_is_faulty == False):
                print("Error! Predriver is faulty!")
                self.predriver_is_faulty = True

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
        if self.flashing:
            await asyncio.sleep(5)
        self.was_system_powered = self.is_system_powered
        self.is_system_powered = self.dbg.fnc.state_power()
        if self.is_system_powered and not self.was_system_powered:
            print("[INFO] System just powered ON. Awaiting flashing.")
            self.is_flashed = False
        # print("System is ON!" if self.is_system_powered else "System is OFF!")
        await asyncio.sleep(0.2)

    # Add statistics of a function:
    # Example: Given a function address (for example, RIOManSlow address), get the timestamp of that record ()
    async def idle_performance(self):
        print("Analyzing IDLE Performance of software")
        self.dbg.cmd('Printer.FILE idle_stat.csv')
        self.dbg.cmd('WinPrint.T.stat.Task /Sort Nesting')
        with open('idle_stat.csv', 'r') as f:
            for line in f:
                line = line.split(",")
            self.idle_performance_done = True

# Check if ECU build number is the one of DPS or Calib. If DPS software, redo tests on DPS
# Check the buildnumber, if the DPS is flashed and running, flash the Calib and redo tests. Initialize to default value the following
# variables: 
    async def func_performance(self, funcName):

        return

    async def run_script(self, script: str):
        """
        Run a PRACTICE CMM script via RCL .cmm with a 20s timeout and robust error handling.
        Uses asyncio.to_thread to avoid blocking the event loop, while showing progress and InterCom state.
        """
        if not self.dbg:
            print("\033[91m[ERROR] TRACE32 not connected.\033[0m")
            self.is_flashed = False
            return

        # Ensure InterCom name is enabled (best-effort)
        try:
            self.dbg.cmd('InterCom.En Karim')
            self.dbg.cmd('ERROR.RESet')
        except Exception as e:
            print(f"[WARN] Could not enable InterCom 'Karim': {e}")

        cleaned_script = self._sanitize_cmm_cmd(script)
        print(f"[INFO] Starting CMM script: {cleaned_script}")

        pbar = tqdm(total=TIMEOUT_TRESHOLD, desc="Flashing in progress", unit="s")
        start_time = time.perf_counter()
        self.flashing = True
        # Run cmm(...) in a thread to keep this coroutine responsive
        self.dbg.cmm(cleaned_script)

        # While the CMM is executing, poll InterCom (informative) and update the progress bar.
        # Note: We cannot cancel CMM easily; we only provide UI feedback here.
        try:
            elapsed_sec = 0
            while elapsed_sec < TIMEOUT_TRESHOLD:
                # Try reading InterCom state
                try:
                    practice_script_state = self.dbg.fnc.intercom_getpracticestate('Karim')
                    if isinstance(practice_script_state, str):
                        if re.match(r"err_[a-z]+", practice_script_state, flags=re.IGNORECASE):
                            print(f"\n\033[91m[ERROR] InterCom reported: {practice_script_state}\033[0m")
                        elif practice_script_state.lower() == "running":
                            # Normal during flashing
                            pass
                        elif practice_script_state.lower() == "stopped":
                            # Will be verified after cmm returns
                            break
                except Exception as e:
                    # Don't fail just because InterCom isn't available
                    print(f"\n[WARN] Could not read InterCom state: {e}")

                await asyncio.sleep(1.0)
                elapsed_sec += 1
                pbar.update(1)

            # Await task completion to surface exceptions, if any
            self.flashing = False
            # After CMM returns, reflect remaining progress (if any)
            elapsed_total = int(time.perf_counter() - start_time)
            print(f"\n{elapsed_total}")
            if elapsed_total < TIMEOUT_TRESHOLD:
                pbar.update(TIMEOUT_TRESHOLD - elapsed_total)

            # Check TRACE32 error flag
            if self.dbg.fnc.error_occurred():
                err_msg = self.dbg.fnc.error_message()
                print(f"\n\033[91m[ERROR] Lauterbach reported an error after CMM: {err_msg}\033[0m")
                try:
                    self.dbg.cmd('ERROR.RESet')
                except Exception:
                    pass
                self.is_flashed = False
            else:
                print("\n\033[92m[OK] CMM script completed successfully.\033[0m")
                self.is_flashed = True

        except TimeoutError:
            # Raised by cmm(timeout=20.0) if PRACTICE didn't finish in time
            print("\n\033[91m[ERROR] CMM script timeout (20s).\033[0m")
            self.is_flashed = False
            try:
                if self.dbg.fnc.error_occurred():
                    print(f"[TRACE32] {self.dbg.fnc.error_message()}")
                    self.dbg.cmd('ERROR.RESet')
            except Exception:
                pass

        except Exception as e:
            print(f"\n\033[91m[ERROR] Unexpected while executing CMM: {e}\033[0m")
            print(traceback.format_exc())
            self.is_flashed = False

        finally:
            # Final InterCom state check (optional)
            try:
                practice_script_state = self.dbg.fnc.intercom_getpracticestate('Karim')
                if isinstance(practice_script_state, str) and practice_script_state.lower().startswith("err_"):
                    print(f"\n\033[91m[ERROR] InterCom final state: {practice_script_state}\033[0m")
                    try:
                        self.dbg.cmd('ERROR.RESet')
                    except Exception:
                        pass
                    self.is_flashed = False
                elif practice_script_state == "stopped" and not self.dbg.fnc.error_occurred():
                    if self.is_flashed is not False:
                        print("\033[92m[OK] InterCom state: stopped.\033[0m")
                        self.is_flashed = True
            except Exception as e:
                print(f"[WARN] Could not read InterCom final state: {e}")

            pbar.close()


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
        asyncio.create_task(self.handle_user_input())

        while True:
            await self.poll_power_state()
            if self.is_flashed and self.is_system_powered and not self.routine_checks_done:
                await self.check_program_running()
            elif self.is_flashed and not self.is_system_powered:
                self.is_flashed = False
                self.routine_checks_done = False
            # if not self.idle_performance_done:
            #    await self.idle_performance()

if __name__ == "__main__":
    handler = T32TestHandler()
    asyncio.run(handler.main_loop())