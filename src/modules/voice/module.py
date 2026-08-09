from src.modules.module_contract import load_manifest

MANIFEST = load_manifest(__import__("pathlib").Path(__file__).parent)
