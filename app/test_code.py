import os
import subprocess

def get_user(user_id):
    query = "SELECT * FROM users WHERE id = " + user_id
    return query

def run_command(cmd):
    subprocess.run(cmd, shell=True)

def process_items(items):
    result = []
    for item in items:
        result.append(item["value"] * 2)
    return result

PASSWORD = "hardcoded_secret_123"
