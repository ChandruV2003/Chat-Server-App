import datetime
import flask
import redis
import json
from datetime import datetime,timedelta
import logging
from flask import jsonify
from flask import send_from_directory
import os
from flask import Flask, request, jsonify, Response, session, redirect, render_template
from werkzeug.utils import secure_filename
import os
import redis
import json
from datetime import datetime
from flask import request
from flask import abort
from flask import Flask, session, Response, stream_with_context
import logging


app = flask.Flask('chat-server-sse')
app.secret_key = 'somerandomkey'
app.config['DEBUG'] = True
app.config['UPLOAD_FOLDER'] = 'uploads'  # Ensure this directory exists
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif'}
r = redis.StrictRedis()
logging.basicConfig(level=logging.DEBUG)
redis_hash = 'chatdata:transcripts'

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def home():
    if 'user' not in flask.session:
        return flask.redirect('/login')
    user = flask.session['user']
    print(f"User {user} logged in.")
    return flask.render_template('home.html', user=user)


def event_stream(user):
    print(f'starting listener for {user}')
    pubsub = r.pubsub()
    pubsub.subscribe(user)
    for message in pubsub.listen():
        data = message['data']
        if type(data) == bytes:
            yield 'data: {}\n\n'.format(data.decode())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form['user']
        session['user'] = user
        # Set a flag in Redis to indicate that the user is logged in
        r.set(f"user:{user}:online", "true")
        return redirect('/')
    return render_template('login.html')

@app.route('/logout')
def logout():
    user = session.pop('user', None)
    if user:
        # Remove the flag from Redis when the user logs out
        r.delete(f"user:{user}:online")
    return redirect('/login')

@app.route('/online-users')
def online_users():
    current_user = session.get('user', None)  # Get the current user from the session
    try:
        keys = r.keys("user:*:online")  # Fetch all keys that match the pattern for online users
        online_users = [key.decode().split(':')[1] for key in keys if key.decode().split(':')[1] != current_user]
        return jsonify(online_users)
    except Exception as e:
        print("Exception in /online-users:", e)
        return Response(str(e), status=500)

# Revised Flask route for handling posts
@app.route('/post', methods=['POST'])
def post():
    user = session.get('user', 'anonymous')
    now = datetime.now().isoformat()
    to = request.form.get('to', '')

    logging.debug("Received post request with user: %s and recipient: %s", user, to)
    logging.debug("Request files: %s", request.files)
    logging.debug("Request form: %s", request.form)

    if not to:
        logging.error("No recipient specified")
        return jsonify({"error": "Recipient not specified"}), 400

    file = request.files.get('file')
    message = request.form.get('message', '').strip()

    # Handle file upload
    if file and allowed_file(file.filename):
        return handle_file_upload(file, user, now, to)

    # Handle text message
    elif message:
        return handle_text_message(message, user, now, to)

    # If neither file nor message content is present
    else:
        logging.debug("Files in request: %s", request.files)
        logging.error("No content to send")
        return jsonify({"error": "No content to send"}), 400

def handle_file_upload(file, user, timestamp, recipient):
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        file.save(file_path)
        logging.info(f"File saved at {file_path}")
    except Exception as e:
        logging.error(f"Failed to save file: {str(e)}")
        return jsonify({"error": "File saving failed"}), 500

    message_data = {
        'username': user,
        'timestamp': timestamp,
        'file': filename,
        'file_path': file_path,
        'message': ""  # Optionally include a message if relevant
    }
    r.publish(recipient, json.dumps(message_data))
    return jsonify({'filename': filename, 'message': 'File uploaded successfully'})

def handle_text_message(message, user, timestamp, recipient):
    message_data = {
        'username': user,
        'timestamp': timestamp,
        'message': message
    }
    r.publish(recipient, json.dumps(message_data))
    return jsonify({'message': 'Message sent successfully'})

@app.route('/stream')
def stream_chat():
    user = session.get('user')
    if not user:
        return Response("Error: No user logged in", status=401)

    response = Response(message_event_stream(user), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    return response

def message_event_stream(user):
    pubsub = r.pubsub()
    pubsub.subscribe(user)  # Subscribe to a user-specific channel
    try:
        for message in pubsub.listen():
            if message['type'] == 'message' and isinstance(message['data'], bytes):
                yield 'data: {}\n\n'.format(message['data'].decode())
    except GeneratorExit:
        pubsub.unsubscribe(user)
        pubsub.close()

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(file_path):
        print("File not found:", file_path)  # Debug output
        abort(404)  # Return a 404 if the file does not exist
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/file-stream')
def file_stream():
    return Response(file_upload_event_stream(), mimetype='text/event-stream')

def file_upload_event_stream():
    pubsub = r.pubsub()
    pubsub.subscribe('file-uploads')  # Subscribe to a general channel for file uploads
    try:
        for message in pubsub.listen():
            if message['type'] == 'message' and isinstance(message['data'], bytes):
                yield 'data: {}\n\n'.format(message['data'].decode())
    except GeneratorExit:
        pubsub.unsubscribe('file-uploads')
        pubsub.close()

@app.route('/is-online/<username>')
def is_online(username):
    is_online = r.get(f"user:{username}:online") == b"true"
    return jsonify({'is_online': is_online})


if __name__ =="__main__":
    app.run(debug=True, port=8080, host="0.0.0.0")