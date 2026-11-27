"""Temporary shared list of feature ideas. Remove this module, its template and nav entry when no longer needed."""
from datetime import date
from uuid import uuid4

from flask import Blueprint, Flask, Response, abort, flash, g, redirect, render_template, request, url_for

from .storage import Store

MAX_IDEA = 1000


def register_todo(app: Flask, store: Store) -> None:
    pages = Blueprint("todo", __name__)

    @pages.route("/todo", methods=["GET", "POST"])
    def index() -> Response | str:
        if request.method == "GET":
            ideas = store.read().get("todos", {})
            return render_template("todo.html", ideas=list(reversed(ideas.values())))
        text = request.form.get("text", "").strip()
        if not text or len(text) > MAX_IDEA:
            abort(400, f"Describe the feature in 1 to {MAX_IDEA:,} characters.")
        key = uuid4().hex
        with store.edit() as library:
            library.setdefault("todos", {})[key] = {
                "id": key, "text": text, "author": g.user, "added_on": date.today().isoformat(),
            }
        flash("Idea added to the TODO list.", "success")
        return redirect(url_for("todo.index"))

    @pages.post("/todo/<key>/remove")
    def remove(key: str) -> Response:
        with store.edit() as library:
            if library.get("todos", {}).pop(key, None) is None:
                abort(404)
        flash("Idea removed.", "success")
        return redirect(url_for("todo.index"))

    app.register_blueprint(pages)
