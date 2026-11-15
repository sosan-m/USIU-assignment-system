from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from functools import wraps
import sqlite3
import os

app = Flask(__name__)
app.secret_key = 'usiu-sfe4015-secret-key'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'zip'}

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    conn = sqlite3.connect('assignment_system.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL,
            department TEXT,
            student_id TEXT
        );
        
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_code TEXT UNIQUE NOT NULL,
            course_name TEXT NOT NULL,
            department TEXT NOT NULL,
            instructor_id INTEGER,
            FOREIGN KEY (instructor_id) REFERENCES users (id)
        );
        
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            course_id INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            total_marks INTEGER DEFAULT 100,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id)
        );
        
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            grade INTEGER,
            feedback TEXT,
            status TEXT DEFAULT 'submitted',
            FOREIGN KEY (assignment_id) REFERENCES assignments (id),
            FOREIGN KEY (student_id) REFERENCES users (id)
        );
    ''')
    
    # Create default admin account if not exists
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        hashed_password = generate_password_hash('admin123')
        cursor.execute("""
            INSERT INTO users (username, password, full_name, email, role, department)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ('admin', hashed_password, 'System Administrator', 'admin@usiu.ac.ke', 'admin', 'Administration'))
    
    conn.commit()
    conn.close()

@app.route('/')
def index():
    if 'user_id' in session:
        if session['role'] == 'student':
            return redirect(url_for('student_dashboard'))
        elif session['role'] == 'instructor':
            return redirect(url_for('instructor_dashboard'))
        else:
            return redirect(url_for('admin_dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['role'] = user['role']
            flash('Login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        full_name = request.form['full_name']
        email = request.form['email']
        role = request.form['role']
        department = request.form.get('department', '')
        student_id = request.form.get('student_id', '')
        
        hashed_password = generate_password_hash(password)
        
        try:
            conn = get_db()
            conn.execute("""
                INSERT INTO users (username, password, full_name, email, role, department, student_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (username, hashed_password, full_name, email, role, department, student_id))
            conn.commit()
            conn.close()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists', 'error')
    
    return render_template('register.html')


def student_required(view):
    """Allow only logged-in students to access a route."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first', 'error')
            return redirect(url_for('login'))

        if session.get('role') != 'student':
            flash('Students only', 'error')
            return redirect(url_for('index'))

        return view(*args, **kwargs)

    return wrapped


def parse_due_date(value):
    """Parse a due date stored as 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM'."""
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == '%Y-%m-%d':
                parsed = parsed.replace(hour=23, minute=59)
            return parsed
        except (ValueError, TypeError):
            continue
    return None

@app.route('/student/dashboard')
@student_required
def student_dashboard():
    conn = get_db()
    rows = conn.execute("""
        SELECT a.id, a.title, a.description, a.due_date, a.total_marks,
               c.course_code, c.course_name,
               s.id AS submission_id, s.submitted_at, s.grade, s.status
        FROM assignments a
        JOIN courses c ON a.course_id = c.id
        LEFT JOIN submissions s
               ON s.assignment_id = a.id AND s.student_id = ?
        ORDER BY a.due_date ASC
    """, (session['user_id'],)).fetchall()
    conn.close()

    now = datetime.now()
    assignments = []
    for row in rows:
        due = parse_due_date(row['due_date'])
        assignment = dict(row)
        assignment['is_overdue'] = bool(due and now > due)
        assignments.append(assignment)
    return render_template('student_dashboard.html', assignments=assignments)

@app.route('/student/submit/<int:assignment_id>', methods=['GET', 'POST'])
@student_required
def submit_assignment(assignment_id):
    conn = get_db()
    assignment = conn.execute("""
        SELECT a.*, c.course_code, c.course_name
        FROM assignments a JOIN courses c ON a.course_id = c.id
        WHERE a.id = ?
    """, (assignment_id,)).fetchone()

    if not assignment:
        conn.close()
        flash('Assignment not found', 'error')
        return redirect(url_for('student_dashboard'))

    existing = conn.execute(
        'SELECT id FROM submissions WHERE assignment_id = ? AND student_id = ?',
        (assignment_id, session['user_id'])
    ).fetchone()
    due = parse_due_date(assignment['due_date'])
    past_due = bool(due and datetime.now() > due)

    if request.method == 'POST':
        if existing:
            conn.close()
            flash('You have already submitted this assignment', 'error')
            return redirect(url_for('student_dashboard'))
        if past_due:
            conn.close()
            flash('The deadline for this assignment has passed', 'error')
            return redirect(url_for('student_dashboard'))

        file = request.files.get('file')
        if not file or file.filename == '':
            conn.close()
            flash('Please choose a file to upload', 'error')
            return redirect(request.url)
        if not allowed_file(file.filename):
            conn.close()
            flash('File type not allowed. Use: ' + ', '.join(sorted(ALLOWED_EXTENSIONS)), 'error')
            return redirect(request.url)

        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        stored_name = f"{assignment_id}_{session['user_id']}_{timestamp}_{secure_filename(file.filename)}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], stored_name))

        submitted_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("""
            INSERT INTO submissions (assignment_id, student_id, file_path, submitted_at)
            VALUES (?, ?, ?, ?)
        """, (assignment_id, session['user_id'], stored_name, submitted_at))
        conn.commit()
        conn.close()
        flash('Assignment submitted successfully!', 'success')
        return redirect(url_for('student_dashboard'))

    conn.close()
    return render_template('submit_assignment.html', assignment=assignment,
                           already_submitted=bool(existing), past_due=past_due,
                           allowed_extensions=sorted(ALLOWED_EXTENSIONS))


    
@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))

@app.route('/lecturer/dashboard')
def instructor_dashboard():


    if 'user_id' not in session or session.get('role') != 'instructor':
        flash('Please log in as a lecturer.', 'error')
        return redirect(url_for('login'))

    conn = get_db()

    courses = conn.execute(
        'SELECT * FROM courses WHERE instructor_id = ?',
        (session['user_id'],)
    ).fetchall()

    assignments = conn.execute('''
        SELECT a.*, c.course_code,
        COUNT(s.id) AS submission_count
        FROM assignments a
        JOIN courses c ON a.course_id = c.id
        LEFT JOIN submissions s ON a.id = s.assignment_id
        WHERE c.instructor_id = ?
        GROUP BY a.id
        ORDER BY a.created_at DESC
    ''', (session['user_id'],)).fetchall()

    conn.close()

    return render_template(
        'lecturer_dashboard.html',
        courses=courses,
        assignments=assignments
    )

@app.route('/lecturer/assignments/create', methods=['GET', 'POST'])
def create_assignment():
    if 'user_id' not in session or session.get('role') != 'instructor':
        flash('Please log in as a lecturer.', 'error')
        return redirect(url_for('login'))

    conn = get_db()

    courses = conn.execute(
        'SELECT * FROM courses WHERE instructor_id = ?',
        (session['user_id'],)
    ).fetchall()

    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        course_id = request.form['course_id']
        due_date = request.form['due_date']
        total_marks = request.form['total_marks']

        course = conn.execute(
            'SELECT id FROM courses WHERE id = ? AND instructor_id = ?',
            (course_id, session['user_id'])
        ).fetchone()

        if not course:
            conn.close()
            flash('Please select a valid course.', 'error')
            return redirect(url_for('create_assignment'))

        conn.execute('''
            INSERT INTO assignments
            (title, description, course_id, due_date, total_marks)
            VALUES (?, ?, ?, ?, ?)
        ''', (title, description, course_id, due_date, total_marks))

        conn.commit()
        conn.close()

        flash('Assignment created successfully!', 'success')
        return redirect(url_for('instructor_dashboard'))

    conn.close()

    return render_template(
        'create_assignment.html',
        courses=courses
    )

@app.route('/lecturer/assignments/<int:assignment_id>/submissions')
def view_submissions(assignment_id):

    if 'user_id' not in session or session.get('role') != 'instructor':
        flash('Please log in as a lecturer.', 'error')
        return redirect(url_for('login'))

    conn = get_db()

    # Check that this assignment belongs to the logged-in lecturer
    assignment = conn.execute('''
        SELECT a.*, c.course_code, c.course_name
        FROM assignments a
        JOIN courses c ON a.course_id = c.id
        WHERE a.id = ? AND c.instructor_id = ?
    ''', (assignment_id, session['user_id'])).fetchone()

    if not assignment:
        conn.close()
        flash('Assignment not found.', 'error')
        return redirect(url_for('instructor_dashboard'))

    # Retrieve submissions for this assignment
    submissions = conn.execute('''
        SELECT s.*, u.full_name, u.student_id, u.email
        FROM submissions s
        JOIN users u ON s.student_id = u.id
        WHERE s.assignment_id = ?
        ORDER BY s.submitted_at DESC
    ''', (assignment_id,)).fetchall()

    conn.close()

    return render_template(
        'view_submissions.html',
        assignment=assignment,
        submissions=submissions
    )

@app.route('/lecturer/submissions/<int:submission_id>/file')
def view_submission_file(submission_id):

    if 'user_id' not in session or session.get('role') != 'instructor':
        flash('Please log in as a lecturer.', 'error')
        return redirect(url_for('login'))

    conn = get_db()

    submission = conn.execute('''
        SELECT s.file_path, s.assignment_id
        FROM submissions s
        JOIN assignments a ON s.assignment_id = a.id
        JOIN courses c ON a.course_id = c.id
        WHERE s.id = ? AND c.instructor_id = ?
    ''', (submission_id, session['user_id'])).fetchone()

    conn.close()

    if not submission:
        flash('Submission not found or access denied.', 'error')
        return redirect(url_for('instructor_dashboard'))

    filename = os.path.basename(submission['file_path'])
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    if not os.path.isfile(file_path):
        flash('The submitted file could not be found.', 'error')
        return redirect(url_for(
            'view_submissions',
            assignment_id=submission['assignment_id']
        ))

    return send_file(file_path, as_attachment=False)

@app.route('/lecturer/submissions/<int:submission_id>/grade', methods=['GET', 'POST'])
def grade_submission(submission_id):

    if 'user_id' not in session or session.get('role') != 'instructor':
        flash('Please log in as a lecturer.', 'error')
        return redirect(url_for('login'))

    conn = get_db()

    submission = conn.execute('''
        SELECT s.*, u.full_name, u.student_id, u.email,
               a.title, a.total_marks, a.id AS assignment_id,
               c.course_code
        FROM submissions s
        JOIN users u ON s.student_id = u.id
        JOIN assignments a ON s.assignment_id = a.id
        JOIN courses c ON a.course_id = c.id
        WHERE s.id = ? AND c.instructor_id = ?
    ''', (submission_id, session['user_id'])).fetchone()

    if not submission:
        conn.close()
        flash('Submission not found.', 'error')
        return redirect(url_for('instructor_dashboard'))

    if request.method == 'POST':
        try:
            grade = int(request.form['grade'])
            feedback = request.form.get('feedback', '')

            if grade < 0 or grade > submission['total_marks']:
                flash('Grade must be within the assignment marks.', 'error')
            else:
                conn.execute('''
                    UPDATE submissions
                    SET grade = ?, feedback = ?, status = 'graded'
                    WHERE id = ?
                ''', (grade, feedback, submission_id))

                conn.commit()
                conn.close()

                flash('Grade saved successfully!', 'success')
                return redirect(url_for(
                    'view_submissions',
                    assignment_id=submission['assignment_id']
                ))

        except ValueError:
            flash('Please enter a valid grade.', 'error')

    conn.close()

    return render_template(
        'grade_submission.html',
        submission=submission
    )
    

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
