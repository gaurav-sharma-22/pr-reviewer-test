import os
import subprocess

# hardcoded secret
API_KEY = "sk-1234567890abcdef"

def run_command(user_input):
    # command injection vulnerability
    subprocess.call("ls " + user_input, shell=True)

def divide(a, b):
    # no zero division check
    return a / b
