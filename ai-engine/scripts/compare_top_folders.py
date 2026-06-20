import os
import filecmp

top_folders = ["app/ports", "app/ml", "app/llm", "app/dataflows", "app/quant"]
target_dirs = ["app/application", "app/domain", "app/infrastructure", "app/presentation", "tests", "scripts", "workflows"]

def compare_file_globally(src_path, filename):
    for target_dir in target_dirs:
        for root, _, files in os.walk(target_dir):
            if filename in files:
                dest_path = os.path.join(root, filename)
                if filecmp.cmp(src_path, dest_path, shallow=False):
                    return dest_path, "identical"
                else:
                    return dest_path, "different"
    return None, "unique"

print("=== ANALYSIS OF TOP-LEVEL FOLDERS ===")
for folder in top_folders:
    if not os.path.exists(folder):
        print(f"\nFolder not found: {folder}")
        continue
    
    print(f"\n--- Analysing folder: {folder} ---")
    identical = []
    different = []
    unique = []
    
    for root, _, files in os.walk(folder):
        for file in files:
            src_path = os.path.join(root, file)
            dest_path, status = compare_file_globally(src_path, file)
            if status == "identical":
                identical.append((src_path, dest_path))
            elif status == "different":
                different.append((src_path, dest_path))
            else:
                unique.append(src_path)
                
    print(f"Identical Duplicates: {len(identical)}")
    for src, dest in identical[:10]:
         print(f"  {src} -> {dest}")
    if len(identical) > 10:
         print(f"  ... and {len(identical) - 10} more")
         
    print(f"Different Counterparts: {len(different)}")
    for src, dest in different:
         print(f"  {src} -> {dest}")
         
    print(f"Unique Files (Need to be moved or reviewed): {len(unique)}")
    for src in unique:
         print(f"  {src}")
