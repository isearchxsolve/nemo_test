import subprocess
import time

def main():
    # Start demo app
    subprocess.Popen(["python", "tests/fixtures/demo_app/app.py"],
                     cwd="D:\\nemo_test",
                     shell=True)
    time.sleep(5)
    
    # Run URL mode
    result = subprocess.run(["openpath", "run", "--mode", "url", "--url", "http://127.0.0.1:5002"],
                            cwd="D:\\nemo_test",
                            capture_output=True,
                            text=True)
    
    # Output results
    print(result.stdout)
    print(result.stderr)
    
    # Exit with test result
    exit(result.returncode)

if __name__ == "__main__":
    main()
