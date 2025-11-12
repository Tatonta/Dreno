import lauterbach.trace32.rcl as t32
import keyboard
import os
import subprocess
import time
from tqdm import tqdm
import re
import asyncio

TIMEOUT_TRESHOLD = 20

T32PPCPath = r"C:\T32N\bin\windows64\t32mppc.exe"
T32ConfigFile = r"C:\T32N\config.t32"

# Script paths
SCRIPTS = {
    "dps": 'do C:\\Git\\ibcu\\Tools\\Lauterbach\\T32SingleWinConfig\\spc58nn_setup_DPS.cmm',
    "calib": 'do C:\\Git\\ibcu\\Tools\\Lauterbach\\T32SingleWinConfig\\spc58nn_setup_Calib.cmm',
    "flashing_window": 'do C:\\T32N\\bin\\windows64\\FlashingDaRitardati.cmm'
}

async def watchdog_test(dbg):
    dbg.variable.write("Fail_Chk", 0)

async def openloop_test(dbg):
    dbg.variable.write("c_BCSW_OpenloopTest", 1)
    dbg.variable.write("c_BCSW_EnFaultRcvyReq", 1)
    time.sleep(0.01)
    dbg.variable.write("c_BCSW_EnFaultRcvyReq", 0)
    pressed_time = 0
    # while(keyboard.is_pressed('o')):
    #     pressed_time = pressed_time + 1
    #     dbg.variable.write("CAL_SRV_OpenLoopPWM", pressed_time*10)
    #     await asyncio.sleep(0.3)
    dbg.variable.write("CAL_SRV_OpenLoopPWM", 100)
    return

def run_script(dbg, script):
    cpu = dbg.fnc('CPU()')
    dbg.cmd('InterCom.En Karim')
    print(cpu)
    dbg.cmd(script)

    scriptTimeOut = 0
    pbar = tqdm(total=TIMEOUT_TRESHOLD, desc="Flashing in progress", unit="s")

    while scriptTimeOut < TIMEOUT_TRESHOLD:
        practice_script_state = dbg.fnc.intercom_getpracticestate('Karim')
        pbar.update(1)
        if practice_script_state == "running":
            time.sleep(0.5)
            scriptTimeOut += 1
            continue
        if re.compile(r"err_[a-z]{,}").match(practice_script_state):
            print("\033[91mFlashing failed. Unlucky. Exiting")
            print("Error: " + practice_script_state + "\033[0m")
            dbg.cmd('ERROR.RESet')
            break
        if practice_script_state == "stopped":
            if dbg.fnc.error_occurred():
                print(dbg.fnc.error_message())
                print("\033[91mFlashing failed. Unlucky. Exiting\033[0m")
                dbg.cmd('ERROR.RESet')
            else:
                print("\033[92mFlashed ECU successfully. Exiting\033[0m")
            break

    pbar.update(19)
    pbar.close()

    
async def main():
    # Start TRACE32 instance
    # command = [T32PPCPath, '-c', T32ConfigFile]
    # process = subprocess.Popen(command)
    # time.sleep(3)

    dbg = t32.connect(node='10.39.79.250', port=20000, protocol="TCP", timeout=10.0)

    # dbg.cmd(SCRIPTS["flashing_window"])
    
    print("Press 'D' to flash DPS script.")
    print("Press 'C' to flash Calibration script.")
    print("Press 'S' to Check CPU status (Det_ReportError has been hit)")
    print("Press 'Esc' to exit.")
    # print(dbg.cmd('CORE.List'))
    # print(dbg.fnc.state_run())
    while True:
        try:
            if keyboard.is_pressed('s'):
                if(dbg.fnc.state_run() == False):
                    print("Program stopped! Currently executed functions: ")
                    for i in range(3):
                        dbg.cmd('CORE.Select ' + str(i))
                        break_address = dbg.address.from_string(dbg.fnc.pp())
                        print("Core " + str(i) + ": " + dbg.fnc.symbol_name(break_address) + " || address: " + str(break_address))
                    dbg.cmd('CORE.Select 0')
            if keyboard.is_pressed('d'):
                print("[INFO] 'D' key detected. Flashing DPS...")
                run_script(dbg, SCRIPTS['dps'])
            elif keyboard.is_pressed('c'):
                print("[INFO] 'C' key detected. Flashing Calibration...")
                run_script(dbg, SCRIPTS['calib'])
            elif keyboard.is_pressed('esc'):
                print("[INFO] Exiting...")
            elif keyboard.is_pressed('o'):
                await openloop_test(dbg)
            elif keyboard.is_pressed('r'):
                await watchdog_test(dbg)
            time.sleep(0.1)
        except KeyboardInterrupt:
            print("[INFO] Interrupted by user.")
            break


if __name__ == "__main__":
    asyncio.run(main())