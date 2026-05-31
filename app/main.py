"""Flask web app for storing machines and waking them via Wake-on-LAN."""
from __future__ import annotations

from flask import Flask, flash, redirect, render_template, request, url_for

from . import storage
from .wol import send_magic_packet


def create_app() -> Flask:
    app = Flask(__name__)
    # Flash messages need a secret key. It is not security-sensitive here
    # (no auth, trusted LAN), so a static fallback is acceptable.
    app.secret_key = "wolw-flash-secret"

    @app.route("/")
    def index():
        machines = storage.list_machines()
        return render_template("index.html", machines=machines)

    @app.post("/machines")
    def add_machine():
        try:
            storage.add_machine(
                name=request.form.get("name", ""),
                mac=request.form.get("mac", ""),
                broadcast=request.form.get("broadcast", storage.DEFAULT_BROADCAST),
                port=request.form.get("port", storage.DEFAULT_PORT) or storage.DEFAULT_PORT,
            )
            flash(f"Added {request.form.get('name')}.", "success")
        except (ValueError, TypeError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("index"))

    @app.post("/machines/<mac>/wake")
    def wake_machine(mac: str):
        machine = storage.get_machine(mac)
        if machine is None:
            flash("Machine not found.", "error")
            return redirect(url_for("index"))
        try:
            send_magic_packet(
                mac=machine["mac"],
                broadcast=machine.get("broadcast", storage.DEFAULT_BROADCAST),
                port=int(machine.get("port", storage.DEFAULT_PORT)),
            )
            flash(f"Magic packet sent to {machine['name']}.", "success")
        except OSError as exc:
            flash(f"Failed to send packet: {exc}", "error")
        return redirect(url_for("index"))

    @app.post("/machines/<mac>/delete")
    def delete_machine(mac: str):
        if storage.delete_machine(mac):
            flash("Machine deleted.", "success")
        else:
            flash("Machine not found.", "error")
        return redirect(url_for("index"))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
