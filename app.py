import sqlite3
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = 'super_secret_key_change_me'
DB_NAME = 'database.db'

DEFAULT_ADMIN_LOGIN = 'admin'
DEFAULT_ADMIN_PASSWORD = 'admin123'

scheduler = BackgroundScheduler(timezone='Europe/Moscow')
scheduler.start()


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT,
                       first_name TEXT, last_name TEXT, patronymic TEXT, email TEXT)''')
    cursor.execute("SELECT * FROM users WHERE username = ?", (DEFAULT_ADMIN_LOGIN,))
    if not cursor.fetchone():
        cursor.execute('''INSERT INTO users (username, password, role, first_name, last_name, patronymic, email) 
                          VALUES (?, ?, ?, ?, ?, ?, ?)''',
                       (DEFAULT_ADMIN_LOGIN, generate_password_hash(DEFAULT_ADMIN_PASSWORD), 'admin',
                        'Главный', 'Администратор', '', 'admin@miracle.local'))

    cursor.execute('''CREATE TABLE IF NOT EXISTS clients 
                      (id INTEGER PRIMARY KEY, last_name TEXT, first_name TEXT, patronymic TEXT, 
                       dob TEXT, description TEXT, phone TEXT, email TEXT, position TEXT)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS companies 
                      (id INTEGER PRIMARY KEY, name TEXT, country TEXT, activity TEXT, 
                       type TEXT, website TEXT, description TEXT)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS company_employees 
                      (id INTEGER PRIMARY KEY, company_id INTEGER, client_id INTEGER, status TEXT DEFAULT 'РАБОТАЕТ',
                       FOREIGN KEY(company_id) REFERENCES companies(id), 
                       FOREIGN KEY(client_id) REFERENCES clients(id))''')

    # ОБНОВЛЕНО: добавлено client_id для личных мероприятий физ. лиц
    cursor.execute('''CREATE TABLE IF NOT EXISTS events 
                      (id INTEGER PRIMARY KEY, company_id INTEGER, project_id INTEGER, client_id INTEGER, event_type TEXT, 
                       start_date TEXT, end_date TEXT, responsible_user TEXT, 
                       description TEXT, status TEXT DEFAULT 'planned',
                       result TEXT, completion_desc TEXT, rating INTEGER)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS projects 
                      (id INTEGER PRIMARY KEY, name TEXT, project_type TEXT, status TEXT, 
                       end_date TEXT, area TEXT, address TEXT, budget TEXT, cp_amount TEXT)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS project_companies 
                      (id INTEGER PRIMARY KEY, project_id INTEGER, company_id INTEGER,
                       FOREIGN KEY(project_id) REFERENCES projects(id),
                       FOREIGN KEY(company_id) REFERENCES companies(id),
                       UNIQUE(project_id, company_id))''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS report_settings 
                      (id INTEGER PRIMARY KEY, recipient_ids TEXT, frequency TEXT, 
                       day_value INTEGER, time_value TEXT)''')

    conn.commit()
    conn.close()


init_db()


# ================= ФУНКЦИИ ОТПРАВКИ ОТЧЕТОВ =================
def send_report_email(report_type='weekly'):
    print(f"\n{'=' * 60}\n[DEBUG] 🚀 ЗАПУСК ОТПРАВКИ: {report_type.upper()}\n{'=' * 60}")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 0. Получаем настройки получателей СРАЗУ
    db_id = 1 if report_type == 'weekly' else 2
    settings = cursor.execute("SELECT recipient_ids FROM report_settings WHERE id = ?", (db_id,)).fetchone()
    if not settings or not settings[0]:
        print("[DEBUG] ОШИБКА: Нет получателей для этого типа отчета.")
        conn.close()
        return False

    recipient_ids = settings[0].split(',')
    placeholders = ','.join('?' * len(recipient_ids))
    recipients = cursor.execute(f"SELECT email FROM users WHERE id IN ({placeholders})", recipient_ids).fetchall()
    emails_to_send = [r[0] for r in recipients if r[0]]

    if not emails_to_send:
        print("[DEBUG] ОШИБКА: У получателей не указана почта.")
        conn.close()
        return False

    # 1. Расчет дат периода
    now = datetime.datetime.now()
    if report_type == 'weekly':
        period_end = now
        period_start = now - datetime.timedelta(days=7)
        next_period_end = now + datetime.timedelta(days=7)
        next_period_start = now
        period_name = "ЕЖЕНЕДЕЛЬНЫЙ ОТЧЕТ / WEEKLY REPORT"
    else:
        period_end = now
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_period_end = now + datetime.timedelta(days=30)
        next_period_start = now
        period_name = "ЕЖЕМЕСЯЧНЫЙ ОТЧЕТ / MONTHLY REPORT"

    period_start_str = period_start.strftime("%d.%m.%Y %H:%M")
    period_end_str = period_end.strftime("%d.%m.%Y %H:%M")
    next_period_start_str = next_period_start.strftime("%d.%m.%Y")
    next_period_end_str = next_period_end.strftime("%d.%m.%Y")

    # 2. Получаем всех пользователей, КРОМЕ главного админа
    cursor.execute("SELECT username, first_name, last_name, patronymic, role FROM users WHERE role != 'admin'")
    users = cursor.fetchall()

    event_types_list = [
        "М2. Звонок/Письмо", "М3. Встреча с клиентом", "М4. Встреча с партнером",
        "М5. Получение запроса КП", "М6. Изменение запроса КП", "М7. Отправка КП",
        "М7.1. Повторная отправка КП", "М8. Получение заказа"
    ]

    # 3. Запросы данных
    cursor.execute('''
        SELECT responsible_user, status, event_type, start_date, description, result, completion_desc
        FROM events WHERE date(start_date) >= date(?) AND date(start_date) <= date(?)
        ORDER BY responsible_user, event_type, start_date
    ''', (period_start.strftime("%Y-%m-%d"), period_end.strftime("%Y-%m-%d")))
    past_events = cursor.fetchall()

    cursor.execute('''
        SELECT responsible_user, event_type, start_date, description
        FROM events WHERE status = 'planned' AND date(start_date) >= date(?) AND date(start_date) <= date(?)
        ORDER BY responsible_user, event_type, start_date
    ''', (next_period_start.strftime("%Y-%m-%d"), next_period_end.strftime("%Y-%m-%d")))
    future_events = cursor.fetchall()

    cursor.execute('''
        SELECT start_date, event_type, responsible_user, description, result, completion_desc
        FROM events WHERE status = 'completed' AND date(start_date) >= date(?) AND date(start_date) <= date(?)
        ORDER BY start_date DESC
    ''', (period_start.strftime("%Y-%m-%d"), period_end.strftime("%Y-%m-%d")))
    completed_events_detail = cursor.fetchall()

    conn.close()  # Теперь закрываем безопасно

    # 4. Структурирование данных
    user_data = {}
    for u in users:
        username = u[0]
        name = f"{u[1]} {u[2]}".strip() or username
        user_data[username] = {
            'name': name,
            'stats': {et: {'planned': [], 'completed': [], 'next_planned': []} for et in event_types_list}
        }

    for ev in past_events:
        resp = ev[0] or "Не назначен"
        if resp not in user_data:
            user_data[resp] = {'name': resp,
                               'stats': {et: {'planned': [], 'completed': [], 'next_planned': []} for et in
                                         event_types_list}}
        status, etype, date, desc = ev[1], ev[2], ev[3], ev[4]
        if etype in user_data[resp]['stats']:
            event_str = f"{date}: {desc}" if desc else f"{date}"
            if status == 'planned':
                user_data[resp]['stats'][etype]['planned'].append(event_str)
            elif status == 'completed':
                user_data[resp]['stats'][etype]['completed'].append(event_str)

    for ev in future_events:
        resp = ev[0] or "Не назначен"
        if resp not in user_data:
            user_data[resp] = {'name': resp,
                               'stats': {et: {'planned': [], 'completed': [], 'next_planned': []} for et in
                                         event_types_list}}
        etype, date, desc = ev[1], ev[2], ev[3]
        if etype in user_data[resp]['stats']:
            event_str = f"({date}) {desc}" if desc else f"({date})"
            user_data[resp]['stats'][etype]['next_planned'].append(event_str)

    # 7. Генерация HTML письма
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #0F172A; background-color: #F8FAFC; padding: 20px; margin: 0;">
        <div style="max-width: 900px; margin: 0 auto; background: #FFFFFF; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h2 style="color: #1E3A8A; text-align: center; border-bottom: 2px solid #38BDF8; padding-bottom: 10px; margin-top: 0;">{period_name}</h2>
            <p style="text-align: center; font-size: 1.1em; color: #64748B; margin-bottom: 30px;">
                За период <strong>{period_start_str}</strong> - <strong>{period_end_str}</strong>
            </p>

            <h3 style="color: #0F172A; border-left: 4px solid #F97316; padding-left: 10px;">1. Статистика и план за отчетный период</h3>
    """

    # Генерация таблиц для каждого пользователя
    for username, data in sorted(user_data.items()):
        html += f"""
            <div style="margin-top: 30px; margin-bottom: 20px; page-break-inside: avoid;">
                <h4 style="color: #1E3A8A; margin-bottom: 10px; background: #F1F5F9; padding: 8px; border-radius: 4px;">
                    Менеджер по продажам / Sales manager: {data['name']}
                </h4>
                <table style="border-collapse: collapse; width: 100%; font-size: 0.85em; border: 1px solid #CBD5E1;">
                    <thead>
                        <tr style="background-color: #E2E8F0;">
                            <th style="border: 1px solid #CBD5E1; padding: 8px; width: 20%; text-align: left;">Вид мероприятия</th>
                            <th style="border: 1px solid #CBD5E1; padding: 8px; width: 26%; text-align: left;">План за отчетный период</th>
                            <th style="border: 1px solid #CBD5E1; padding: 8px; width: 26%; text-align: left;">Факт за отчетный период</th>
                            <th style="border: 1px solid #CBD5E1; padding: 8px; width: 28%; text-align: left;">План на след. период ({next_period_start_str} - {next_period_end_str})</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for etype in event_types_list:
            stats = data['stats'][etype]
            planned_count = len(stats['planned'])
            completed_count = len(stats['completed'])
            next_planned_count = len(stats['next_planned'])

            planned_list = "<br>".join(
                [f"• {item}" for item in stats['planned']]) or "<span style='color:#94A3B8'>-</span>"
            completed_list = "<br>".join(
                [f"• {item}" for item in stats['completed']]) or "<span style='color:#94A3B8'>-</span>"
            next_planned_list = "<br>".join(
                [f"• {item}" for item in stats['next_planned']]) or "<span style='color:#94A3B8'>-</span>"

            html += f"""
                        <tr>
                            <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;"><strong>{etype}</strong></td>
                            <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;">
                                <strong style="color: #2563EB;">{planned_count}</strong><br>
                                <span style="color: #64748B; font-size: 0.9em;">{planned_list}</span>
                            </td>
                            <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;">
                                <strong style="color: #059669;">{completed_count}</strong><br>
                                <span style="color: #64748B; font-size: 0.9em;">{completed_list}</span>
                            </td>
                            <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;">
                                <strong style="color: #D97706;">{next_planned_count}</strong><br>
                                <span style="color: #64748B; font-size: 0.9em;">{next_planned_list}</span>
                            </td>
                        </tr>
            """
        html += """
                    </tbody>
                </table>
            </div>
        """

    # Раздел 2: Детализация
    html += """
            <h3 style="color: #0F172A; border-left: 4px solid #10B981; padding-left: 10px; margin-top: 40px;">2. Детализация по выполненным мероприятиям за отчетный период</h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 0.85em; border: 1px solid #CBD5E1; margin-top: 15px;">
                <thead>
                    <tr style="background-color: #E2E8F0;">
                        <th style="border: 1px solid #CBD5E1; padding: 8px; width: 12%;">Дата исполнения</th>
                        <th style="border: 1px solid #CBD5E1; padding: 8px; width: 18%;">Вид</th>
                        <th style="border: 1px solid #CBD5E1; padding: 8px; width: 20%;">Исполнитель / Ответственный</th>
                        <th style="border: 1px solid #CBD5E1; padding: 8px; width: 25%;">Название / Описание</th>
                        <th style="border: 1px solid #CBD5E1; padding: 8px; width: 25%;">Результат</th>
                    </tr>
                </thead>
                <tbody>
    """

    if completed_events_detail:
        for ev in completed_events_detail:
            date = ev[0]
            etype = ev[1]
            resp = ev[2] or "Не назначен"
            desc = ev[3] or "Без описания"
            result = ev[4] or "Не указан"
            comp_desc = ev[5]

            result_str = f"<strong style='color: #059669;'>{result}</strong>"
            if comp_desc:
                result_str += f"<br><span style='color: #64748B; font-size: 0.9em;'>{comp_desc}</span>"

            # Получаем полное имя ответственного, если он есть в базе
            resp_name = resp
            for u in users:
                if u[0] == resp:
                    resp_name = f"{u[1]} {u[2]}".strip() or resp
                    break

            html += f"""
                    <tr>
                        <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;">{date}</td>
                        <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;">{etype}</td>
                        <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;">{resp_name}</td>
                        <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;">{desc}</td>
                        <td style="border: 1px solid #CBD5E1; padding: 8px; vertical-align: top;">{result_str}</td>
                    </tr>
            """
    else:
        html += """
                    <tr>
                        <td colspan="5" style="border: 1px solid #CBD5E1; padding: 15px; text-align: center; color: #64748B;">
                            За отчетный период нет выполненных мероприятий.
                        </td>
                    </tr>
        """

    html += """
                </tbody>
            </table>

            <p style="margin-top: 40px; color: #94A3B8; font-size: 12px; text-align: center; border-top: 1px solid #E2E8F0; padding-top: 15px;">
                С уважением, автоматическая система Miracle_2.0<br>
                Дата формирования: """ + datetime.datetime.now().strftime('%d.%m.%Y %H:%M') + """
            </p>
        </div>
    </body>
    </html>
    """

    # --- ЯНДЕКС.ПОЧТА ---
    SMTP_SERVER = "smtp.yandex.ru"
    SMTP_PORT = 465
    SENDER_EMAIL = "Mikele208ID@yandex.ru"  # Ваша реальная почта
    SENDER_PASSWORD = "ymgemicktalhwaaq"  # Ваш пароль приложения

    # --- GMAIL ---
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 465
    SENDER_EMAIL = "mkachin9@gmail.com"  # Ваша реальная почта
    SENDER_PASSWORD = "gkhkmkbkahlaqvgt"  # Ваш пароль приложения (16 символов)

    SMTP_SERVER = "smtp.mail.ru"
    SMTP_PORT = 465
    SENDER_EMAIL = "mikemike_000@mail.ru"
    SENDER_PASSWORD = "QcPdkjr2jakwI4WNgget"

    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = ", ".join(emails_to_send)
        msg['Subject'] = period_name
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.set_debuglevel(0)
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print(f"✅ УСПЕХ: {report_type} отчет отправлен на {', '.join(emails_to_send)}")
        return True
    except Exception as e:
        print(f"❌ ОШИБКА ОТПРАВКИ: {e}")
        return False


def send_daily_plan_email(test_mode=False):
    print(f"\n{'=' * 60}\n[DEBUG] 🚀 ЗАПУСК ОТПРАВКИ ЕЖЕДНЕВНОГО ПЛАНА (Тест: {test_mode})\n{'=' * 60}")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # id=3 для ежедневных планов
    settings = cursor.execute("SELECT recipient_ids, time_value FROM report_settings WHERE id = 3").fetchone()
    if not settings or not settings[0]:
        print("[DEBUG] ОШИБКА: Нет получателей для ежедневного плана.")
        conn.close()
        return False

    recipient_ids = settings[0].split(',')
    placeholders = ','.join('?' * len(recipient_ids))

    # Получаем данные пользователей (имя и почта)
    users = cursor.execute(f"SELECT id, username, first_name, last_name, email FROM users WHERE id IN ({placeholders})",
                           recipient_ids).fetchall()

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    today_display = datetime.datetime.now().strftime("%d.%m.%Y")

    success_count = 0

    for user in users:
        user_id, username, first_name, last_name, email = user
        if not email:
            continue

        full_name = f"{first_name} {last_name}".strip() or username

        # Получаем ТОЛЬКО запланированные мероприятия этого пользователя НА СЕГОДНЯ
        cursor.execute('''
            SELECT event_type, start_date, description, company_id, project_id, client_id
            FROM events 
            WHERE responsible_user = ? AND status = 'planned' AND date(start_date) = ?
            ORDER BY start_date
        ''', (username, today_str))
        events = cursor.fetchall()

        # Формируем персонализированное письмо
        subject_prefix = "[ТЕСТ] " if test_mode else ""
        subject = f"{subject_prefix}📅 Ваш план мероприятий на {today_display}"

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #0F172A; background-color: #F8FAFC; padding: 20px; margin: 0;">
            <div style="max-width: 600px; margin: 0 auto; background: #FFFFFF; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                <h2 style="color: #1E3A8A; border-bottom: 2px solid #38BDF8; padding-bottom: 10px; margin-top: 0;">Добрый день, {full_name}!</h2>
                <p style="font-size: 1.1em; color: #64748B;">Направляем вам список запланированных мероприятий на сегодня (<strong>{today_display}</strong>):</p>
        """

        if events:
            html_body += '<ul style="line-height: 1.6; color: #334155;">'
            for ev in events:
                etype, date, desc, comp_id, proj_id, client_id = ev
                # Определяем контекст (Компания, Проект или Личное)
                context = ""
                if comp_id:
                    comp_name = cursor.execute("SELECT name FROM companies WHERE id = ?", (comp_id,)).fetchone()
                    context = f" (Компания: {comp_name[0]})" if comp_name else ""
                elif proj_id:
                    proj_name = cursor.execute("SELECT name FROM projects WHERE id = ?", (proj_id,)).fetchone()
                    context = f" (Проект: {proj_name[0]})" if proj_name else ""

                desc_text = f"<br><small style='color: #64748B;'>{desc}</small>" if desc else ""
                html_body += f"<li style='margin-bottom: 15px;'><strong>{etype}</strong>{context}{desc_text}</li>"
            html_body += '</ul>'
        else:
            html_body += "<p style='color: #059669; font-weight: bold; background: #ECFDF5; padding: 15px; border-radius: 6px;'>✅ На сегодня новых мероприятий не запланировано. Отличного дня!</p>"

        html_body += """
                <p style="margin-top: 30px; color: #94A3B8; font-size: 12px; border-top: 1px solid #E2E8F0; padding-top: 15px; text-align: center;">
                    С уважением, автоматическая система Miracle_2.0
                </p>
            </div>
        </body>
        </html>
        """

        # Отправка персонального письма
        try:
            msg = MIMEMultipart()
            msg['From'] = "mikemike_000@mail.ru"  # Замените на ваш SMTP email
            msg['To'] = email
            msg['Subject'] = subject
            msg.attach(MIMEText(html_body, 'html'))

            with smtplib.SMTP_SSL("smtp.mail.ru", 465) as server:  # Замените на ваши SMTP настройки
                server.login("mikemike_000@mail.ru", "QcPdkjr2jakwI4WNgget")  # Замените на ваши данные
                server.send_message(msg)

            print(f"✅ Письмо отправлено: {full_name} ({email})")
            success_count += 1
        except Exception as e:
            print(f"❌ Ошибка отправки для {full_name}: {e}")

    conn.close()
    print(f"[DEBUG] Всего успешно отправлено: {success_count} из {len(users)}")
    return success_count > 0


def update_scheduler():
    scheduler.remove_all_jobs()
    conn = sqlite3.connect(DB_NAME)
    weekly = conn.cursor().execute(
        "SELECT recipient_ids, day_value, time_value FROM report_settings WHERE id = 1").fetchone()
    monthly = conn.cursor().execute(
        "SELECT recipient_ids, day_value, time_value FROM report_settings WHERE id = 2").fetchone()
    daily = conn.cursor().execute(
        "SELECT recipient_ids, time_value FROM report_settings WHERE id = 3").fetchone()  # НОВОЕ
    conn.close()

    if weekly and weekly[0]:
        day_int = int(weekly[1])
        hour, minute = map(int, weekly[2].split(':'))
        scheduler.add_job(send_report_email, 'cron', day_of_week=day_int, hour=hour, minute=minute, args=['weekly'],
                          id='weekly_report', timezone='Europe/Moscow')

    if monthly and monthly[0]:
        day_int = int(monthly[1])
        if day_int < 1: day_int = 1
        hour, minute = map(int, monthly[2].split(':'))
        scheduler.add_job(send_report_email, 'cron', day=day_int, hour=hour, minute=minute, args=['monthly'],
                          id='monthly_report', timezone='Europe/Moscow')

    # НОВОЕ: Ежедневный план
    if daily and daily[0]:
        hour, minute = map(int, daily[1].split(':'))
        print(f"[SCHEDULER] ✅ Ежедневный план: каждый день в {hour:02d}:{minute:02d} (МСК)")
        scheduler.add_job(send_daily_plan_email, 'cron', hour=hour, minute=minute, id='daily_plan',
                          timezone='Europe/Moscow')

    print(f"[SCHEDULER] 📋 Активные задачи: {[job.id for job in scheduler.get_jobs()]}")


update_scheduler()


def login_required(f):
    def wrap(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Пожалуйста, войдите в систему.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    wrap.__name__ = f.__name__
    return wrap


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect(DB_NAME)
        user = conn.cursor().execute(
            "SELECT password, role, first_name, last_name, patronymic FROM users WHERE username = ?",
            (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user[0], password):
            session['logged_in'] = True
            session['username'] = username
            session['role'] = user[1]
            session['first_name'] = user[2] or ''
            session['last_name'] = user[3] or ''
            session['patronymic'] = user[4] or ''
            return redirect(url_for('main'))
        flash('Неверный логин или пароль!', 'error')
    return render_template('login.html')


@app.route('/main')
@login_required
def main(): return render_template('main.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ================= АДМИН ПАНЕЛЬ =================
@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if session.get('role') not in ['admin', 'local_admin']:
        flash('Нет прав', 'error');
        return redirect(url_for('main'))
    if request.method == 'POST':
        if 'new_password' in request.form:
            new_pass = request.form['new_password']
            if new_pass == request.form['confirm_password'] and len(new_pass) >= 4:
                conn = sqlite3.connect(DB_NAME)
                conn.cursor().execute("UPDATE users SET password = ? WHERE username = ?",
                                      (generate_password_hash(new_pass), session['username']))
                conn.commit();
                conn.close()
                flash('Пароль изменен!', 'success')
            else:
                flash('Ошибка смены пароля', 'error')
        elif 'create_user' in request.form:
            first_name, last_name = request.form.get('first_name', '').strip(), request.form.get('last_name',
                                                                                                 '').strip()
            patronymic, email = request.form.get('patronymic', '').strip(), request.form.get('email', '').strip()
            new_username, new_password, new_role = request.form.get('username', '').strip(), request.form.get(
                'password', '').strip(), request.form.get('role', '')
            if not first_name or not last_name or not email or not new_username or not new_password:
                flash('Заполните все обязательные поля!', 'error')
            else:
                conn = sqlite3.connect(DB_NAME)
                if conn.cursor().execute("SELECT id FROM users WHERE username = ?", (new_username,)).fetchone():
                    flash('Логин уже занят!', 'error')
                else:
                    conn.cursor().execute(
                        '''INSERT INTO users (username, password, role, first_name, last_name, patronymic, email) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (new_username, generate_password_hash(new_password), new_role, first_name, last_name,
                         patronymic, email))
                    conn.commit();
                    flash(f'Пользователь {first_name} {last_name} успешно создан!', 'success')
                conn.close()
    users_list = sqlite3.connect(DB_NAME).cursor().execute(
        "SELECT id, username, role, first_name, last_name, email FROM users").fetchall()
    return render_template('admin.html', users_list=users_list)


@app.route('/admin/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'POST':
        first_name, last_name = request.form.get('first_name', '').strip(), request.form.get('last_name', '').strip()
        patronymic, email = request.form.get('patronymic', '').strip(), request.form.get('email', '').strip()
        new_username, new_role, new_password = request.form.get('username', '').strip(), request.form.get('role',
                                                                                                          ''), request.form.get(
            'password', '').strip()
        if conn.cursor().execute("SELECT id FROM users WHERE username = ? AND id != ?",
                                 (new_username, user_id)).fetchone():
            flash('Логин занят!', 'error')
        else:
            if new_password:
                conn.cursor().execute(
                    '''UPDATE users SET username=?, role=?, password=?, first_name=?, last_name=?, patronymic=?, email=? WHERE id=?''',
                    (new_username, new_role, generate_password_hash(new_password), first_name, last_name, patronymic,
                     email, user_id))
            else:
                conn.cursor().execute(
                    '''UPDATE users SET username=?, role=?, first_name=?, last_name=?, patronymic=?, email=? WHERE id=?''',
                    (new_username, new_role, first_name, last_name, patronymic, email, user_id))
            conn.commit();
            flash('Пользователь обновлен!', 'success');
            return redirect(url_for('admin'))
    user = conn.cursor().execute(
        "SELECT id, username, role, first_name, last_name, patronymic, email FROM users WHERE id = ?",
        (user_id,)).fetchone()
    conn.close()
    return render_template('edit_user.html', user=user)


@app.route('/admin/reports', methods=['GET', 'POST'])
@login_required
def admin_reports():
    if session.get('role') not in ['admin', 'local_admin']:
        flash('Нет прав', 'error');
        return redirect(url_for('main'))
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'POST':
        if 'save_settings' in request.form:
            report_type = request.form.get('report_type')

            if report_type == 'daily':
                db_id = 3
                recipients = request.form.getlist('recipients')
                time_value = request.form.get('time_value')
                recipient_str = ','.join(recipients) if recipients else ''
                # Для daily day_value не нужен, ставим 0
                conn.cursor().execute(
                    '''INSERT OR REPLACE INTO report_settings (id, recipient_ids, frequency, day_value, time_value) VALUES (?, ?, 'daily', 0, ?)''',
                    (db_id, recipient_str, time_value))
            else:
                db_id = 1 if report_type == 'weekly' else 2
                recipients = request.form.getlist('recipients')
                day_value = request.form.get('day_value')
                time_value = request.form.get('time_value')
                recipient_str = ','.join(recipients) if recipients else ''
                conn.cursor().execute(
                    '''INSERT OR REPLACE INTO report_settings (id, recipient_ids, frequency, day_value, time_value) VALUES (?, ?, ?, ?, ?)''',
                    (db_id, recipient_str, report_type, day_value, time_value))

            conn.commit()
            update_scheduler()
            flash(f'Настройки {"ежедневного " if report_type == "daily" else ""}отчета сохранены!', 'success')

        elif 'test_daily' in request.form and session.get('role') == 'admin':
            if send_daily_plan_email(test_mode=True):
                flash('Тест ежедневного плана отправлен выбранным сотрудникам!', 'success')
            else:
                flash('Ошибка отправки или нет выбранных сотрудников с почтой.', 'error')

        # ... (оставьте существующие блоки test_weekly и test_monthly без изменений) ...
        elif 'test_weekly' in request.form and session.get('role') == 'admin':
            if send_report_email('weekly'):
                flash('Тест еженедельного отчета отправлен!', 'success')
            else:
                flash('Ошибка отправки.', 'error')
        elif 'test_monthly' in request.form and session.get('role') == 'admin':
            if send_report_email('monthly'):
                flash('Тест ежемесячного отчета отправлен!', 'success')
            else:
                flash('Ошибка отправки.', 'error')

    weekly_set = conn.cursor().execute(
        "SELECT recipient_ids, day_value, time_value FROM report_settings WHERE id = 1").fetchone()
    monthly_set = conn.cursor().execute(
        "SELECT recipient_ids, day_value, time_value FROM report_settings WHERE id = 2").fetchone()
    w_recipients = weekly_set[0].split(',') if weekly_set and weekly_set[0] else []
    m_recipients = monthly_set[0].split(',') if monthly_set and monthly_set[0] else []
    users = conn.cursor().execute(
        "SELECT id, first_name, last_name, email FROM users WHERE email IS NOT NULL AND email != ''").fetchall()

    daily_set = conn.cursor().execute("SELECT recipient_ids, time_value FROM report_settings WHERE id = 3").fetchone()
    d_recipients = daily_set[0].split(',') if daily_set and daily_set[0] else []
    conn.close()
    return render_template('report_settings.html', users=users,
                           w_recipients=w_recipients, w_day=weekly_set[1] if weekly_set else 0,
                           w_time=weekly_set[2] if weekly_set else '13:00',
                           m_recipients=m_recipients, m_day=monthly_set[1] if monthly_set else 1,
                           m_time=monthly_set[2] if monthly_set else '13:00',
                           d_recipients=d_recipients, d_time=daily_set[1] if daily_set else '09:00')


# ================= ФИЗ. ЛИЦА =================
@app.route('/clients')
@login_required
def clients(): return render_template('clients.html')


@app.route('/clients/physical')
@login_required
def clients_physical():
    cl_list = sqlite3.connect(DB_NAME).cursor().execute(
        "SELECT id, last_name, first_name, patronymic, phone, email, position FROM clients").fetchall()
    return render_template('clients_physical.html', clients=cl_list)


@app.route('/clients/companies/add', methods=['GET', 'POST'])
@login_required
def add_company():
    # 1. Проверяем, пришли ли мы из проекта (через URL ?project_id=X)
    project_id = request.args.get('project_id', type=int)

    if request.method == 'POST':
        # 2. При отправке формы также проверяем скрытое поле (на всякий случай)
        project_id = request.form.get('project_id', type=int) or project_id

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Создаем компанию как обычно
        cursor.execute('''INSERT INTO companies (name, country, activity, type, website, description) 
                          VALUES (?, ?, ?, ?, ?, ?)''',
                       (request.form.get('name', ''), request.form.get('country', ''),
                        request.form.get('activity', ''), request.form.get('type', ''),
                        request.form.get('website', ''), request.form.get('description', '')))
        company_id = cursor.lastrowid

        # 3. ГЛАВНОЕ: Если был указан project_id, сразу добавляем связь в таблицу project_companies
        if project_id:
            cursor.execute("INSERT INTO project_companies (project_id, company_id) VALUES (?, ?)",
                           (project_id, company_id))

        conn.commit()
        conn.close()

        # 4. Умный возврат: если создавали из проекта, возвращаемся в проект. Иначе в общий список.
        if project_id:
            return redirect(url_for('view_project', project_id=project_id))
        return redirect(url_for('clients_companies'))

    # Для GET-запроса (просто открытие формы) готовим данные
    conn = sqlite3.connect(DB_NAME)
    available_clients = conn.cursor().execute("SELECT id, last_name, first_name, patronymic FROM clients").fetchall()
    conn.close()

    return render_template('company_form.html', company=None, is_editing=False, employees=[],
                           planned_events=[], completed_events=[], cancelled_events=[],
                           users_list=[], available_clients=available_clients, project_id=project_id)


@app.route('/clients/physical/add', methods=['GET', 'POST'])
@login_required
def add_client_physical():
    # Проверяем, пришли ли мы из компании
    company_id = request.args.get('company_id', type=int)

    if request.method == 'POST':
        company_id = request.form.get('company_id', type=int) or company_id

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO clients (last_name, first_name, patronymic, dob, description, phone, email, position) 
                          VALUES (?,?,?,?,?,?,?,?)''',
                       (request.form.get('last_name', ''), request.form.get('first_name', ''),
                        request.form.get('patronymic', ''),
                        request.form.get('dob', ''), request.form.get('description', ''), request.form.get('phone', ''),
                        request.form.get('email', ''), request.form.get('position', '')))
        client_id = cursor.lastrowid

        # Если был указан company_id, сразу добавляем сотрудника в компанию
        if company_id:
            cursor.execute("INSERT INTO company_employees (company_id, client_id, status) VALUES (?, ?, 'РАБОТАЕТ')",
                           (company_id, client_id))

        conn.commit()
        conn.close()

        # Возвращаемся в компанию, если создавали оттуда
        if company_id:
            return redirect(url_for('view_company', company_id=company_id))
        return redirect(url_for('clients_physical'))

    return render_template('client_form.html', client=None, is_editing=False, company_id=company_id)

@app.route('/clients/physical/<int:client_id>')
@login_required
def view_client_physical(client_id):
    conn = sqlite3.connect(DB_NAME)
    client = conn.cursor().execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client: conn.close(); return redirect(url_for('clients_physical'))

    employers = conn.cursor().execute(
        '''SELECT c.id, c.name, ce.status FROM company_employees ce JOIN companies c ON ce.company_id = c.id WHERE ce.client_id = ?''',
        (client_id,)).fetchall()

    # Личные мероприятия физ. лица
    client_events = conn.cursor().execute(
        '''SELECT id, event_type, start_date, end_date, responsible_user, description, status, result, completion_desc, rating FROM events WHERE client_id = ? ORDER BY start_date DESC''',
        (client_id,)).fetchall()
    conn.close()

    return render_template('client_form.html', client=client, is_editing=False, employers=employers,
                           planned_events=[e for e in client_events if e[6] == 'planned'],
                           completed_events=[e for e in client_events if e[6] == 'completed'],
                           cancelled_events=[e for e in client_events if e[6] == 'cancelled'])


@app.route('/clients/physical/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_client_physical(client_id):
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'POST':
        conn.cursor().execute(
            '''UPDATE clients SET last_name=?, first_name=?, patronymic=?, dob=?, description=?, phone=?, email=?, position=? WHERE id=?''',
            (request.form.get('last_name', ''), request.form.get('first_name', ''), request.form.get('patronymic', ''),
             request.form.get('dob', ''), request.form.get('description', ''), request.form.get('phone', ''),
             request.form.get('email', ''), request.form.get('position', ''), client_id))
        conn.commit();
        conn.close()
        flash('Данные обновлены!', 'success');
        return redirect(url_for('view_client_physical', client_id=client_id))
    client = conn.cursor().execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    conn.close()
    return render_template('client_form.html', client=client, is_editing=True) if client else redirect(
        url_for('clients_physical'))


# Мероприятия Физ. лица
@app.route('/clients/physical/<int:client_id>/add_event', methods=['POST'])
@login_required
def add_client_event(client_id):
    responsible_user = session['username'] if session['role'] == 'manager' else request.form.get('responsible_user',
                                                                                                 session['username'])
    planned_date = request.form.get('planned_date', '')  # ОДНО ПОЛЕ ДАТЫ

    conn = sqlite3.connect(DB_NAME)
    conn.cursor().execute('''INSERT INTO events (client_id, company_id, project_id, event_type, start_date, end_date, responsible_user, description, status) 
                             VALUES (?, NULL, NULL, ?, ?, NULL, ?, ?, 'planned')''',
                          (client_id, request.form.get('event_type', ''), planned_date, responsible_user,
                           request.form.get('description', '')))
    conn.commit();
    conn.close()
    flash('Мероприятие создано!', 'success')
    return redirect(url_for('view_client_physical', client_id=client_id))


@app.route('/clients/physical/<int:client_id>/complete_event/<int:event_id>')
@login_required
def complete_client_event(client_id, event_id):
    conn = sqlite3.connect(DB_NAME);
    conn.cursor().execute("UPDATE events SET status = 'completed' WHERE id = ?", (event_id,));
    conn.commit();
    conn.close()
    return redirect(url_for('view_client_physical', client_id=client_id))


@app.route('/clients/physical/<int:client_id>/cancel_event/<int:event_id>')
@login_required
def cancel_client_event(client_id, event_id):
    conn = sqlite3.connect(DB_NAME);
    conn.cursor().execute("UPDATE events SET status = 'cancelled' WHERE id = ?", (event_id,));
    conn.commit();
    conn.close()
    return redirect(url_for('view_client_physical', client_id=client_id))


@app.route('/clients/physical/<int:client_id>/edit_event/<int:event_id>', methods=['GET', 'POST'])
@login_required
def edit_client_event(client_id, event_id):
    conn = sqlite3.connect(DB_NAME)
    event = conn.cursor().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    if not event: return redirect(url_for('view_client_physical', client_id=client_id))
    if request.method == 'POST':
        conn = sqlite3.connect(DB_NAME)
        conn.cursor().execute(
            '''UPDATE events SET status='completed', result=?, completion_desc=?, rating=? WHERE id=?''',
            (request.form.get('result', ''), request.form.get('completion_desc', ''), request.form.get('rating', 0),
             event_id))
        conn.commit();
        conn.close()
        flash('Мероприятие завершено!', 'success');
        return redirect(url_for('view_client_physical', client_id=client_id))
    return render_template('edit_event.html', event=event, company_id=None, client_id=client_id, is_project=False)


@app.route('/clients/physical/<int:client_id>/event/<int:event_id>')
@login_required
def view_client_event(client_id, event_id):
    conn = sqlite3.connect(DB_NAME)
    event = conn.cursor().execute("SELECT * FROM events WHERE id = ? AND client_id = ?",
                                  (event_id, client_id)).fetchone()
    conn.close()
    if not event: return redirect(url_for('view_client_physical', client_id=client_id))
    return render_template('view_event.html', event=event, company_id=None, client_id=client_id, is_project=False)


# ================= КОМПАНИИ =================
@app.route('/clients/companies')
@login_required
def clients_companies():
    com_list = sqlite3.connect(DB_NAME).cursor().execute(
        '''SELECT c.id, c.name, c.type, c.activity, c.website, c.country, COUNT(ce.client_id) as emp_count FROM companies c LEFT JOIN company_employees ce ON c.id = ce.company_id GROUP BY c.id''').fetchall()
    return render_template('companies_list.html', companies=com_list)





@app.route('/clients/companies/<int:company_id>')
@login_required
def view_company(company_id):
    conn = sqlite3.connect(DB_NAME)
    comp = conn.cursor().execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    if not comp: conn.close(); return redirect(url_for('clients_companies'))
    employees = conn.cursor().execute(
        '''SELECT cl.id, cl.last_name, cl.first_name, cl.patronymic, cl.position FROM clients cl JOIN company_employees ce ON cl.id = ce.client_id WHERE ce.company_id = ? AND ce.status = 'РАБОТАЕТ' ''',
        (company_id,)).fetchall()
    projects = conn.cursor().execute(
        '''SELECT p.id, p.name, p.status, p.address FROM projects p JOIN project_companies pc ON p.id = pc.project_id WHERE pc.company_id = ?''',
        (company_id,)).fetchall()
    events = conn.cursor().execute(
        '''SELECT id, event_type, start_date, end_date, responsible_user, description, status, result, completion_desc, rating FROM events WHERE company_id = ? ORDER BY start_date DESC''',
        (company_id,)).fetchall()
    users_list = conn.cursor().execute("SELECT username FROM users").fetchall()
    conn.close()
    return render_template('company_form.html', company=comp, is_editing=False, employees=employees, projects=projects,
                           planned_events=[e for e in events if e[6] == 'planned'],
                           completed_events=[e for e in events if e[6] == 'completed'],
                           cancelled_events=[e for e in events if e[6] == 'cancelled'], users_list=users_list)


@app.route('/clients/companies/<int:company_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_company(company_id):
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'POST':
        # Редактируются ВСЕ поля
        conn.cursor().execute(
            '''UPDATE companies SET name=?, country=?, activity=?, type=?, website=?, description=? WHERE id=?''',
            (request.form.get('name', ''), request.form.get('country', ''), request.form.get('activity', ''),
             request.form.get('type', ''), request.form.get('website', ''), request.form.get('description', ''),
             company_id))
        conn.commit();
        conn.close()
        flash('Компания обновлена!', 'success');
        return redirect(url_for('view_company', company_id=company_id))
    comp = conn.cursor().execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    conn.close()
    return render_template('company_form.html', company=comp, is_editing=True, employees=[], projects=[],
                           planned_events=[], completed_events=[], cancelled_events=[],
                           users_list=[]) if comp else redirect(url_for('clients_companies'))


# Мероприятия Компании
@app.route('/clients/companies/<int:company_id>/add_event', methods=['POST'])
@login_required
def add_event(company_id):
    responsible_user = session['username'] if session['role'] == 'manager' else request.form.get('responsible_user',
                                                                                                 session['username'])
    planned_date = request.form.get('planned_date', '')  # ОДНО ПОЛЕ ДАТЫ

    conn = sqlite3.connect(DB_NAME)
    conn.cursor().execute('''INSERT INTO events (company_id, project_id, client_id, event_type, start_date, end_date, responsible_user, description, status) 
                             VALUES (?, NULL, NULL, ?, ?, NULL, ?, ?, 'planned')''',
                          (company_id, request.form.get('event_type', ''), planned_date, responsible_user,
                           request.form.get('description', '')))
    conn.commit();
    conn.close()
    flash('Мероприятие создано!', 'success')
    return redirect(url_for('view_company', company_id=company_id))


@app.route('/clients/companies/<int:company_id>/complete_event/<int:event_id>')
@login_required
def complete_event(company_id, event_id):
    conn = sqlite3.connect(DB_NAME);
    conn.cursor().execute("UPDATE events SET status = 'completed' WHERE id = ?", (event_id,));
    conn.commit();
    conn.close()
    return redirect(url_for('view_company', company_id=company_id))


@app.route('/clients/companies/<int:company_id>/cancel_event/<int:event_id>')
@login_required
def cancel_event(company_id, event_id):
    conn = sqlite3.connect(DB_NAME);
    conn.cursor().execute("UPDATE events SET status = 'cancelled' WHERE id = ?", (event_id,));
    conn.commit();
    conn.close()
    return redirect(url_for('view_company', company_id=company_id))


@app.route('/clients/companies/<int:company_id>/edit_event/<int:event_id>', methods=['GET', 'POST'])
@login_required
def edit_event(company_id, event_id):
    conn = sqlite3.connect(DB_NAME)
    event = conn.cursor().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    if not event: return redirect(url_for('view_company', company_id=company_id))
    if request.method == 'POST':
        conn = sqlite3.connect(DB_NAME)
        conn.cursor().execute(
            '''UPDATE events SET status='completed', result=?, completion_desc=?, rating=? WHERE id=?''',
            (request.form.get('result', ''), request.form.get('completion_desc', ''), request.form.get('rating', 0),
             event_id))
        conn.commit();
        conn.close()
        flash('Мероприятие завершено!', 'success');
        return redirect(url_for('view_company', company_id=company_id))
    return render_template('edit_event.html', event=event, company_id=company_id, client_id=None, is_project=False)


@app.route('/clients/companies/<int:company_id>/event/<int:event_id>')
@login_required
def view_event(company_id, event_id):
    conn = sqlite3.connect(DB_NAME)
    event = conn.cursor().execute("SELECT * FROM events WHERE id = ? AND company_id = ?",
                                  (event_id, company_id)).fetchone()
    conn.close()
    if not event: return redirect(url_for('view_company', company_id=company_id))
    return render_template('view_event.html', event=event, company_id=company_id, client_id=None, is_project=False)


@app.route('/clients/companies/<int:company_id>/select_employee')
@login_required
def select_employee(company_id):
    conn = sqlite3.connect(DB_NAME)
    clients = conn.cursor().execute(
        '''SELECT id, last_name, first_name, patronymic, position FROM clients WHERE id NOT IN (SELECT client_id FROM company_employees WHERE company_id = ? AND status = 'РАБОТАЕТ')''',
        (company_id,)).fetchall()
    conn.close()
    return render_template('select_employee.html', clients=clients, company_id=company_id)


@app.route('/clients/companies/<int:company_id>/link_employee', methods=['POST'])
@login_required
def link_employee(company_id):
    client_id = request.form.get('client_id')
    if client_id:
        conn = sqlite3.connect(DB_NAME)
        try:
            conn.cursor().execute(
                "INSERT INTO company_employees (company_id, client_id, status) VALUES (?,?, 'РАБОТАЕТ')",
                (company_id, client_id))
            conn.commit();
            flash('Сотрудник добавлен!', 'success')
        except sqlite3.IntegrityError:
            conn.cursor().execute(
                "UPDATE company_employees SET status = 'РАБОТАЕТ' WHERE company_id = ? AND client_id = ?",
                (company_id, client_id))
            conn.commit();
            flash('Сотрудник возвращен!', 'success')
        conn.close()
    return redirect(url_for('view_company', company_id=company_id))


@app.route('/clients/companies/<int:company_id>/unlink_employee/<int:client_id>', methods=['POST'])
@login_required
def unlink_employee(company_id, client_id):
    conn = sqlite3.connect(DB_NAME)
    conn.cursor().execute("UPDATE company_employees SET status = 'РАБОТАЛ' WHERE company_id = ? AND client_id = ?",
                          (company_id, client_id))
    conn.commit();
    conn.close()
    flash('Сотрудник удален (статус: РАБОТАЛ)', 'info');
    return redirect(url_for('view_company', company_id=company_id))


# ================= ПРОЕКТЫ =================
@app.route('/clients/projects')
@login_required
def clients_projects():
    proj_list = sqlite3.connect(DB_NAME).cursor().execute(
        '''SELECT p.id, p.name, p.project_type, p.status, p.address, p.budget, COUNT(pc.company_id) as comp_count FROM projects p LEFT JOIN project_companies pc ON p.id = pc.project_id GROUP BY p.id''').fetchall()
    return render_template('projects_list.html', projects=proj_list)


@app.route('/clients/projects/add', methods=['GET', 'POST'])
@login_required
def add_project():
    if request.method == 'POST':
        conn = sqlite3.connect(DB_NAME)
        conn.cursor().execute(
            '''INSERT INTO projects (name, project_type, status, end_date, area, address, budget, cp_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (request.form.get('name', ''), request.form.get('project_type', ''), request.form.get('status', ''),
             request.form.get('end_date', ''), request.form.get('area', ''), request.form.get('address', ''),
             request.form.get('budget', ''), request.form.get('cp_amount', '')))
        conn.commit();
        conn.close()
        return redirect(url_for('clients_projects'))
    return render_template('project_form.html', project=None, is_editing=False, companies=[], planned_events=[],
                           completed_events=[], cancelled_events=[], users_list=[])


@app.route('/clients/projects/<int:project_id>')
@login_required
def view_project(project_id):
    conn = sqlite3.connect(DB_NAME)
    proj = conn.cursor().execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not proj: conn.close(); return redirect(url_for('clients_projects'))
    companies = conn.cursor().execute(
        '''SELECT c.id, c.name, c.type FROM companies c JOIN project_companies pc ON c.id = pc.company_id WHERE pc.project_id = ?''',
        (project_id,)).fetchall()
    events = conn.cursor().execute(
        '''SELECT id, event_type, start_date, end_date, responsible_user, description, status, result, completion_desc, rating FROM events WHERE project_id = ? ORDER BY start_date DESC''',
        (project_id,)).fetchall()
    users_list = conn.cursor().execute("SELECT username FROM users").fetchall()
    conn.close()
    return render_template('project_form.html', project=proj, is_editing=False, companies=companies,
                           planned_events=[e for e in events if e[6] == 'planned'],
                           completed_events=[e for e in events if e[6] == 'completed'],
                           cancelled_events=[e for e in events if e[6] == 'cancelled'], users_list=users_list)


@app.route('/clients/projects/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_project(project_id):
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'POST':
        # Редактируются ВСЕ поля проекта
        conn.cursor().execute(
            '''UPDATE projects SET name=?, project_type=?, status=?, end_date=?, area=?, address=?, budget=?, cp_amount=? WHERE id=?''',
            (request.form.get('name', ''), request.form.get('project_type', ''), request.form.get('status', ''),
             request.form.get('end_date', ''), request.form.get('area', ''), request.form.get('address', ''),
             request.form.get('budget', ''), request.form.get('cp_amount', ''), project_id))
        conn.commit();
        conn.close()
        flash('Проект обновлен!', 'success');
        return redirect(url_for('view_project', project_id=project_id))
    proj = conn.cursor().execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return render_template('project_form.html', project=proj, is_editing=True, companies=[], planned_events=[],
                           completed_events=[], cancelled_events=[], users_list=[]) if proj else redirect(
        url_for('clients_projects'))


# Мероприятия Проекта
@app.route('/clients/projects/<int:project_id>/add_event', methods=['POST'])
@login_required
def add_project_event(project_id):
    responsible_user = session['username'] if session['role'] == 'manager' else request.form.get('responsible_user',
                                                                                                 session['username'])
    planned_date = request.form.get('planned_date', '')  # ОДНО ПОЛЕ ДАТЫ

    conn = sqlite3.connect(DB_NAME)
    conn.cursor().execute('''INSERT INTO events (project_id, company_id, client_id, event_type, start_date, end_date, responsible_user, description, status) 
                             VALUES (?, NULL, NULL, ?, ?, NULL, ?, ?, 'planned')''',
                          (project_id, request.form.get('event_type', ''), planned_date, responsible_user,
                           request.form.get('description', '')))
    conn.commit();
    conn.close()
    flash('Мероприятие создано!', 'success')
    return redirect(url_for('view_project', project_id=project_id))


@app.route('/clients/projects/<int:project_id>/complete_event/<int:event_id>')
@login_required
def complete_project_event(project_id, event_id):
    conn = sqlite3.connect(DB_NAME);
    conn.cursor().execute("UPDATE events SET status = 'completed' WHERE id = ?", (event_id,));
    conn.commit();
    conn.close()
    return redirect(url_for('view_project', project_id=project_id))


@app.route('/clients/projects/<int:project_id>/cancel_event/<int:event_id>')
@login_required
def cancel_project_event(project_id, event_id):
    conn = sqlite3.connect(DB_NAME);
    conn.cursor().execute("UPDATE events SET status = 'cancelled' WHERE id = ?", (event_id,));
    conn.commit();
    conn.close()
    return redirect(url_for('view_project', project_id=project_id))


@app.route('/clients/projects/<int:project_id>/edit_event/<int:event_id>', methods=['GET', 'POST'])
@login_required
def edit_project_event(project_id, event_id):
    conn = sqlite3.connect(DB_NAME)
    event = conn.cursor().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    if not event: return redirect(url_for('view_project', project_id=project_id))
    if request.method == 'POST':
        conn = sqlite3.connect(DB_NAME)
        conn.cursor().execute(
            '''UPDATE events SET status='completed', result=?, completion_desc=?, rating=? WHERE id=?''',
            (request.form.get('result', ''), request.form.get('completion_desc', ''), request.form.get('rating', 0),
             event_id))
        conn.commit();
        conn.close()
        flash('Мероприятие завершено!', 'success');
        return redirect(url_for('view_project', project_id=project_id))
    return render_template('edit_project_event.html', event=event, project_id=project_id)


@app.route('/clients/projects/<int:project_id>/event/<int:event_id>')
@login_required
def view_project_event(project_id, event_id):
    conn = sqlite3.connect(DB_NAME)
    event = conn.cursor().execute("SELECT * FROM events WHERE id = ? AND project_id = ?",
                                  (event_id, project_id)).fetchone()
    conn.close()
    if not event: return redirect(url_for('view_project', project_id=project_id))
    return render_template('view_project_event.html', event=event, project_id=project_id)


@app.route('/clients/projects/<int:project_id>/select_company')
@login_required
def select_company_for_project(project_id):
    conn = sqlite3.connect(DB_NAME)
    companies = conn.cursor().execute(
        '''SELECT id, name, type FROM companies WHERE id NOT IN (SELECT company_id FROM project_companies WHERE project_id = ?)''',
        (project_id,)).fetchall()
    conn.close()
    return render_template('select_company_for_project.html', companies=companies, project_id=project_id)


@app.route('/clients/projects/<int:project_id>/link_company', methods=['POST'])
@login_required
def link_company_to_project(project_id):
    company_id = request.form.get('company_id')
    if company_id:
        conn = sqlite3.connect(DB_NAME)
        try:
            conn.cursor().execute("INSERT INTO project_companies (project_id, company_id) VALUES (?,?)",
                                  (project_id, company_id))
            conn.commit();
            flash('Компания добавлена в проект!', 'success')
        except sqlite3.IntegrityError:
            flash('Компания уже в проекте.', 'error')
        conn.close()
    return redirect(url_for('view_project', project_id=project_id))


@app.route('/clients/projects/<int:project_id>/unlink_company/<int:company_id>', methods=['POST'])
@login_required
def unlink_company_from_project(project_id, company_id):
    conn = sqlite3.connect(DB_NAME)
    conn.cursor().execute("DELETE FROM project_companies WHERE project_id = ? AND company_id = ?",
                          (project_id, company_id))
    conn.commit();
    conn.close()
    flash('Компания удалена из проекта', 'info');
    return redirect(url_for('view_project', project_id=project_id))


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)