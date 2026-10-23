import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from huggingface_hub import snapshot_download
for repo in ["Systran/faster-whisper-small", "Systran/faster-whisper-large-v3"]:
    print(" downloading", repo, flush=True)
    p = snapshot_download(repo, local_dir="D:/LunaAI/xiaoyun/models/" + repo.split("/")[-1])
    print(" OK", p, flush=True)
print("ALL DONE")
