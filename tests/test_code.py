# app/test_code.py
import os
import subprocess

def get_user(user_id):
    # Bug: SQL injection risk
    query = "SELECT * FROM users WHERE id = " + user_id
    return query

def run_command(cmd):
    # Security: shell injection
    subprocess.run(cmd, shell=True)

def process_items(items):
    # Bug: no null check
    result = []
    for item in items:
        result.append(item["value"] * 2)
    return result

PASSWORD = "hardcoded_secret_123"  # Security: hardcoded secret