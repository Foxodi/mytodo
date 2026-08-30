"""python -m mytodo"""
from mytodo.storage.api_store import choose_store, load_online_config
from mytodo.ui.app import TodoApp

def main():
    cfg = load_online_config()
    if cfg:
        print(f"My Todo List — online mode ({cfg['api_url']})")
    app = TodoApp(store=choose_store())
    app.mainloop()

if __name__ == "__main__":
    main()
