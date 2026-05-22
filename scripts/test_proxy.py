import os
import sys

# Add src/ to python path to allow imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from cma.config import settings
from cma.scraper.stealth import get_random_proxy

def run_tests():
    print("--- Starting Proxy Rotation Tests ---")
    
    # Backup current configs
    orig_proxy_server = settings.proxy_server
    orig_proxy_username = settings.proxy_username
    orig_proxy_password = settings.proxy_password
    orig_proxy_list = settings.proxy_list
    
    proxies_txt_backup = None
    if os.path.exists("proxies.txt"):
        with open("proxies.txt", "r", encoding="utf-8") as f:
            proxies_txt_backup = f.read()
        os.remove("proxies.txt")
        print("Backed up existing proxies.txt")

    try:
        # Test 1: No proxy configured
        settings.proxy_server = ""
        settings.proxy_username = ""
        settings.proxy_password = ""
        settings.proxy_list = ""
        
        proxy = get_random_proxy()
        print(f"Test 1 (No Proxy) -> Result: {proxy} (Expected: None)")
        assert proxy is None
        
        # Test 2: Fallback to single proxy without auth
        settings.proxy_server = "127.0.0.1:8080"
        proxy = get_random_proxy()
        print(f"Test 2 (Single Proxy No Auth) -> Result: {proxy} (Expected: {{'server': 'http://127.0.0.1:8080'}})")
        assert proxy == {"server": "http://127.0.0.1:8080"}
        
        # Test 3: Fallback to single proxy with auth
        settings.proxy_username = "user"
        settings.proxy_password = "pass"
        proxy = get_random_proxy()
        print(f"Test 3 (Single Proxy With Auth) -> Result: {proxy} (Expected: server, username, password)")
        assert proxy == {"server": "http://127.0.0.1:8080", "username": "user", "password": "pass"}
        
        # Test 4: Load from proxy_list env var (comma-separated)
        settings.proxy_list = "http://u1:p1@1.1.1.1:80,socks5://2.2.2.2:1080"
        # Reset single proxy settings
        settings.proxy_server = ""
        
        selected_proxies = set()
        for _ in range(20):
            p = get_random_proxy()
            if p:
                selected_proxies.add(p["server"])
        print(f"Test 4 (Proxy List Env) -> Selected servers: {selected_proxies} (Expected: {{'http://1.1.1.1:80', 'socks5://2.2.2.2:1080'}})")
        assert "http://1.1.1.1:80" in selected_proxies or "socks5://2.2.2.2:1080" in selected_proxies
        
        # Test 5: Load from proxies.txt
        with open("proxies.txt", "w", encoding="utf-8") as f:
            f.write("# This is a comment\n")
            f.write("user3:pass3@3.3.3.3:3128\n")
            f.write("http://4.4.4.4:8080\n")
            
        selected_proxies_file = set()
        for _ in range(20):
            p = get_random_proxy()
            if p:
                selected_proxies_file.add(p["server"])
        print(f"Test 5 (Proxies File) -> Selected servers: {selected_proxies_file} (Expected: {{'http://3.3.3.3:3128', 'http://4.4.4.4:8080'}})")
        assert "http://3.3.3.3:3128" in selected_proxies_file or "http://4.4.4.4:8080" in selected_proxies_file
        
        print("\nAll Tests Passed Successfully! ✅")
        
    finally:
        # Restore backups
        settings.proxy_server = orig_proxy_server
        settings.proxy_username = orig_proxy_username
        settings.proxy_password = orig_proxy_password
        settings.proxy_list = orig_proxy_list
        
        if os.path.exists("proxies.txt"):
            os.remove("proxies.txt")
            
        if proxies_txt_backup is not None:
            with open("proxies.txt", "w", encoding="utf-8") as f:
                f.write(proxies_txt_backup)
            print("Restored original proxies.txt")

if __name__ == "__main__":
    run_tests()
