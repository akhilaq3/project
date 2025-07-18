# =========================================
# Imports
# =========================================
from flask import (Flask, render_template, request, redirect, url_for, session,
                   flash, g) # <-- 'g' is the application context global
import sqlite3
import hashlib
import os
import functools
from datetime import datetime, timezone
import traceback

# =========================================
# App Configuration
# =========================================
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_default_very_secret_key_CHANGE_ME_IN_PROD')
DATABASE = 'database.db'

# =========================================
# Database Helper Functions (Flask Application Context Style) - CORRECTED
# =========================================

def get_db():
    """
    Establishes a database connection for the current request context.
    If a connection already exists in 'g', it's reused.
    """
    if 'db' not in g:
        try:
            g.db = sqlite3.connect(DATABASE, timeout=10)
            g.db.row_factory = sqlite3.Row
            # These PRAGMA statements improve concurrency and consistency
            g.db.execute("PRAGMA journal_mode=WAL;")
            g.db.execute("PRAGMA busy_timeout = 5000;")
            g.db.execute("PRAGMA foreign_keys = ON;")
        except sqlite3.Error as e:
            print(f"Database connection error: {e}")
            # If connection fails, we can't proceed. Flash a message.
            flash("Database service is currently unavailable. Please try again later.", "error")
            g.db = None # Ensure g.db is None if connection fails
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    """
    Closes the database connection at the end of the request.
    This function is automatically called by Flask.
    """
    db = g.pop('db', None)
    if db is not None:
        db.close()

def hash_password(password):
    """Hashes a password using SHA256."""
    if isinstance(password, str):
        return hashlib.sha256(password.encode('utf-8')).hexdigest()
    return None

# =========================================
# Inject 'now' for Jinja Templates
# =========================================
@app.context_processor
def inject_now():
    """Makes the current UTC time available to all templates."""
    return {'now': datetime.now(timezone.utc)}

# =========================================
# Decorator for Login Required
# =========================================
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# =========================================
# Landing Page Route
# =========================================
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

# =========================================
# Authentication Routes (Refactored)
# =========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            flash('Username and password are required.', 'error')
            return render_template('login.html')

        hashed_password = hash_password(password)
        db = get_db()
        if not db: # Connection failed
            return render_template('login.html')

        try:
            user = db.execute('SELECT id, username FROM users WHERE username = ? AND password = ?',
                              (username, hashed_password)).fetchone()
            if user:
                session.clear()
                session['user_id'] = user['id']
                session['username'] = user['username']
                session.permanent = True
                next_url = request.args.get('next') or session.pop('next_url', None)
                flash(f"Welcome back, {user['username']}!", "success")
                return redirect(next_url or url_for('dashboard'))
            else:
                flash('Invalid username or password.', 'error')
        except sqlite3.Error as e:
            print(f"DB error during login: {e}")
            flash("An error occurred during login.", "error")

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Username and password are required.', 'error')
            return render_template('register.html', username=username)

        hashed_password = hash_password(password)
        db = get_db()
        if not db: # Connection failed
            return render_template('register.html', username=username)

        try:
            db.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                       (username, hashed_password))
            db.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists.', 'error')
        except sqlite3.Error as e:
            print(f"DB error during registration: {e}")
            flash('An error occurred during registration.', 'error')
            db.rollback() # Rollback on other errors
        
        return render_template('register.html', username=username)

    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been successfully logged out.', 'success')
    return redirect(url_for('login'))

# =========================================
# Core App Routes (Refactored)
# =========================================
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', username=session.get('username'))

@app.route('/year<int:year_num>')
@login_required
def year_view(year_num):
    if not (1 <= year_num <= 4):
        flash("Invalid year specified.", "error")
        return redirect(url_for('dashboard'))
    return render_template(f'year{year_num}.html', year=year_num)

@app.route('/year<int:year_num>/semester<int:semester_num>')
@login_required
def semester_view(year_num, semester_num):
    if not (1 <= year_num <= 4 and 1 <= semester_num <= 2):
        flash("Invalid year or semester specified.", "error")
        return redirect(url_for('dashboard'))
    
    subjects = []
    db = get_db()
    if db:
        try:
            query = """
                SELECT DISTINCT TRIM(subject) as subject FROM courses 
                WHERE year = ? AND semester = ? AND subject IS NOT NULL AND TRIM(subject) != '' 
                ORDER BY LOWER(TRIM(subject))
            """
            subjects = db.execute(query, (year_num, semester_num)).fetchall()
        except sqlite3.Error as e:
            print(f"DB error fetching subjects: {e}")
            flash("Error fetching subjects.", "error")

    return render_template('semester_list.html', subjects=subjects, year=year_num, semester=semester_num)

@app.route('/year<int:year>/semester<int:semester>/subject/<subject>')
@login_required
def subject_detail_view(year, semester, subject):
    if not (1 <= year <= 4 and 1 <= semester <= 2):
        flash("Invalid year or semester.", "error")
        return redirect(url_for('dashboard'))
    
    user_id = session['user_id']
    course = None
    db = get_db()
    if not db:
        return redirect(url_for('semester_view', year_num=year, semester_num=semester))

    try:
        # Fetch both course and performance in one go
        course_query = "SELECT * FROM courses WHERE year = ? AND semester = ? AND LOWER(TRIM(subject)) = LOWER(TRIM(?))"
        course = db.execute(course_query, (year, semester, subject)).fetchone()
    except sqlite3.Error as e:
        print(f"DB error fetching subject details: {e}")
        flash(f"Error retrieving data for {subject}.", "error")
        return redirect(url_for('semester_view', year_num=year, semester_num=semester))

    return render_template('subject_details.html', course=course, subject=subject, year=year, semester=semester)

# =========================================
# Quiz Routes (Refactored)
# =========================================
@app.route('/quiz/<int:year>/<int:semester>/<subject>/<int:unit>')
@login_required
def quiz(year, semester, subject, unit):
    if not (1 <= year <= 4 and 1 <= semester <= 2 and 1 <= unit <= 5):
        flash("Invalid quiz parameters.", "error")
        return redirect(url_for('dashboard'))

    questions = []
    db = get_db()
    if db:
        try:
            query = """
                SELECT id, question_number, question_text, option1, option2, option3, option4 
                FROM quizzes 
                WHERE year = ? AND semester = ? AND LOWER(TRIM(subject)) = LOWER(TRIM(?)) AND unit = ? 
                ORDER BY question_number
            """
            questions = db.execute(query, (year, semester, subject, unit)).fetchall()
        except sqlite3.Error as e:
            print(f"DB error fetching questions: {e}")
            flash("Database error fetching quiz questions.", "error")
    
    if not questions:
        flash(f'No quiz questions found for {subject} - Unit {unit}.', 'warning')
        return redirect(url_for('subject_detail_view', year=year, semester=semester, subject=subject))

    return render_template('quiz.html', questions=questions, year=year, semester=semester, subject=subject, unit=unit)

# =========================================
# Submit Quiz Route (Simplified and Corrected)
# =========================================
@app.route('/submit_quiz', methods=['POST'])
@login_required
def submit_quiz():
    try:
        # --- Get Form Data ---
        year = request.form.get('year', type=int)
        semester = request.form.get('semester', type=int)
        subject = request.form.get('subject')
        unit = request.form.get('unit', type=int)
        user_id = session['user_id']

        if not all([year, semester, subject, unit]):
            flash("Missing quiz information. Submission failed.", "error")
            return redirect(url_for('dashboard'))

        # --- Use a single DB connection for all operations in this request ---
        db = get_db()
        if not db:
            # get_db() already flashes a message
            return redirect(url_for('subject_detail_view', year=year, semester=semester, subject=subject))

        # --- Fetch Data (Read Operations) ---
        course_row = db.execute('SELECT id FROM courses WHERE year = ? AND semester = ? AND LOWER(TRIM(subject)) = LOWER(TRIM(?))',
                                (year, semester, subject)).fetchone()
        if not course_row:
            flash(f"Course '{subject}' not found. Cannot save score.", "error")
            return redirect(url_for('semester_view', year_num=year, semester_num=semester))
        course_id = course_row['id']
        
        questions_data = db.execute('''SELECT id, question_number, question_text, option1, option2, option3, option4, correct_option 
                                       FROM quizzes WHERE year = ? AND semester = ? AND LOWER(TRIM(subject)) = LOWER(TRIM(?)) AND unit = ? 
                                       ORDER BY question_number''', (year, semester, subject, unit)).fetchall()

        # --- Calculate Score & Build Results (No DB access needed) ---
        detailed_results = []
        unit_score = 0
        submitted_answers = {int(k.split('_')[1]): int(v) for k, v in request.form.items() if k.startswith('q_')}

        for q_data in questions_data:
            q_num = q_data['question_number']
            user_answer_num = submitted_answers.get(q_num)
            is_correct = user_answer_num is not None and user_answer_num == q_data['correct_option']
            
            if is_correct:
                unit_score += 1
            
            detailed_results.append({
                'question_text': q_data['question_text'],
                'user_answer_text': q_data[f'option{user_answer_num}'] if user_answer_num else "Not Answered",
                'correct_answer_text': q_data[f'option{q_data["correct_option"]}'],
                'is_correct': is_correct
            })
        
        unit_total_questions = len(questions_data)

        # --- Save Score Attempt (Write Operation) ---
        if unit_total_questions > 0:
            db.execute('''INSERT INTO performance (user_id, course_id, unit, score, total, date) 
                          VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''', 
                       (user_id, course_id, unit, unit_score, unit_total_questions))
            db.commit()
            flash(f'Quiz Submitted! Your score: {unit_score}/{unit_total_questions}', 'success')
        else:
            flash('This quiz has no questions. Score not saved.', 'warning')

        return render_template('quiz_result.html',
                               year=year, semester=semester, subject=subject, unit=unit,
                               unit_score=unit_score, unit_total=unit_total_questions,
                               detailed_results=detailed_results)

    except sqlite3.Error as e:
        print(f"DATABASE ERROR in submit_quiz: {e}")
        traceback.print_exc()
        flash('A database error occurred while submitting your quiz. Please try again.', 'error')
        if 'db' in g and g.db: g.db.rollback() # Rollback changes on error
        # Try to redirect back gracefully
        try:
            return redirect(url_for('subject_detail_view', year=request.form.get('year'), semester=request.form.get('semester'), subject=request.form.get('subject')))
        except:
            return redirect(url_for('dashboard'))
    except Exception as e:
        print(f"UNEXPECTED ERROR in submit_quiz: {e}")
        traceback.print_exc()
        flash('An unexpected error occurred. Please try again.', 'error')
        return redirect(url_for('dashboard'))

# =========================================
# Performance Tracking Route (Refactored)
# =========================================
@app.route('/my_performance')
@login_required
def my_performance():
    user_id = session['user_id']
    performance_data = []
    db = get_db()
    if db:
        try:
            # This query correctly gets the latest attempt for each unit and then aggregates per subject
            query = """
                WITH LatestAttempts AS (
                    SELECT 
                        p.course_id, 
                        p.unit, 
                        p.score, 
                        p.total,
                        ROW_NUMBER() OVER(PARTITION BY p.course_id, p.unit ORDER BY p.date DESC, p.id DESC) as rn 
                    FROM performance p 
                    WHERE p.user_id = ?
                )
                SELECT 
                    c.year, 
                    c.semester, 
                    c.subject, 
                    SUM(la.score) AS total_score_achieved, 
                    SUM(la.total) AS total_questions_possible 
                FROM LatestAttempts la 
                JOIN courses c ON la.course_id = c.id 
                WHERE la.rn = 1 
                GROUP BY la.course_id, c.year, c.semester, c.subject 
                ORDER BY c.year, c.semester, c.subject;
            """
            performance_data = db.execute(query, (user_id,)).fetchall()
        except sqlite3.Error as e:
            print(f"DB error fetching performance summary: {e}")
            flash("Could not retrieve performance data due to a database error.", "error")
            
    return render_template('my_performance.html', performance_data=performance_data)

# =========================================
# Run the App
# =========================================
if __name__ == '__main__':
    # With the application context pattern, it is now safe to run in default threaded mode.
    app.run(debug=True)