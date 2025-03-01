import random
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command

from config.config import TOKEN
from db.database import Database

# Включение логирования с указанием даты и времени
ENABLE_LOGGING = True
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] - %(message)s")

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = Database()

user_data = {}
AVAILABLE_ROLES = ["qa", "sa", "dev_back", "dev_front", "other"]

##############################################################################
# Функция для выборки вопросов из базы данных
##############################################################################
def query_db_for_questions(role=None, difficulty=None, exclude_ids=None):
    """
    Возвращает список вопросов из таблицы questions с фильтрацией:
      - по role (если указан)
      - по difficulty (если указан)
      - исключая вопросы, чьи ID содержатся в exclude_ids.
    Результат сортируется случайным образом.
    """
    exclude_ids = exclude_ids or set()
    base_sql = "SELECT * FROM questions"
    conditions = []
    params = []
    if role is not None:
        conditions.append("role=?")
        params.append(role)
    if difficulty is not None:
        conditions.append("difficulty=?")
        params.append(difficulty)
    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)
    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        if where_clause:
            where_clause += f" AND id NOT IN ({placeholders})"
        else:
            where_clause = f" WHERE id NOT IN ({placeholders})"
        params.extend(list(exclude_ids))
    sql = base_sql + where_clause + " ORDER BY RANDOM()"
    rows = db.execute(sql, tuple(params), fetchall=True)
    return rows

##############################################################################
# Функция pick_n_questions – выбор вопросов с приоритетом
##############################################################################
def pick_n_questions(user_role, difficulty, needed, used_ids):
    result = []
    # 1) Вопросы с user_role и заданной сложностью
    rows_1 = query_db_for_questions(role=user_role, difficulty=difficulty, exclude_ids=used_ids)
    can1 = min(len(rows_1), needed)
    result.extend(rows_1[:can1])
    for r in rows_1[:can1]:
        used_ids.add(r[0])
    needed -= can1
    if needed <= 0:
        return result
    # 2) Вопросы с ролью "other" и той же сложностью
    rows_2 = query_db_for_questions(role="other", difficulty=difficulty, exclude_ids=used_ids)
    can2 = min(len(rows_2), needed)
    result.extend(rows_2[:can2])
    for r in rows_2[:can2]:
        used_ids.add(r[0])
    needed -= can2
    return result

##############################################################################
# Функция pick_20_questions – сбор 20 вопросов для теста
##############################################################################
def pick_20_questions(user_role):
    used_ids = set()
    final_q = []
    final_q.extend(pick_n_questions(user_role, 1, 5, used_ids))
    final_q.extend(pick_n_questions(user_role, 2, 5, used_ids))
    final_q.extend(pick_n_questions(user_role, 3, 5, used_ids))
    if len(final_q) < 20:
        needed = 20 - len(final_q)
        rows_any = query_db_for_questions(role=None, difficulty=None, exclude_ids=used_ids)
        can_take = min(len(rows_any), needed)
        final_q.extend(rows_any[:can_take])
    return final_q[:20]

##############################################################################
# Функция countdown_task – обновление сообщения с обратным отсчётом
##############################################################################
async def countdown_task(message: types.Message, seconds: int):
    for remaining in range(seconds, 0, -1):
        try:
            await message.edit_text(f"Осталось {remaining} секунд...")
        except Exception:
            pass
        await asyncio.sleep(1)
    return

##############################################################################
# Хендлер /start – регистрация или приветствие
##############################################################################
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    row = db.execute("SELECT name, role FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if row:
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Начать тестирование")]], resize_keyboard=True)
        await message.answer(f"С возвращением, {row[0]} (роль: {row[1]})!", reply_markup=kb)
    else:
        user_data[user_id] = {"registration_step": "ask_name"}
        await message.answer("Привет! Введи своё имя:")

##############################################################################
# Хендлер для кнопки "Начать тестирование"
##############################################################################
@dp.message(lambda msg: msg.text == "Начать тестирование")
async def cmd_menu_test(message: types.Message):
    await cmd_test(message)

##############################################################################
# Хендлер /test – начало теста, выбор 20 вопросов
##############################################################################
@dp.message(Command("test"))
async def cmd_test(message: types.Message):
    user_id = message.from_user.id
    row = db.execute("SELECT name, role FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not row:
        await message.answer("Сначала зарегистрируйтесь /start")
        return
    name, role = row
    if not role:
        role = "other"
    if ENABLE_LOGGING:
        logging.info(f"[TEST] user_id={user_id}, name={name}, role={role} => pick_20")
    questions = pick_20_questions(role)
    if not questions:
        await message.answer("Нет вопросов в базе.")
        return
    user_data[user_id] = {"score": 0, "questions": questions, "current_q": 0}
    if ENABLE_LOGGING:
        logging.info(f"[TEST] user_id={user_id}, получено {len(questions)} вопросов (без повторов)")
    await ask_question(message, user_id)

##############################################################################
# Функция ask_question – отправка вопроса, запуск таймера и обратного отсчёта
##############################################################################
async def ask_question(message: types.Message, user_id: int):
    user = user_data.get(user_id)
    if not user:
        return
    # Отменяем старый таймер, если он запущен
    if "timeout_task" in user:
        task = user["timeout_task"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            if ENABLE_LOGGING:
                logging.info(f"[ASK_QUESTION] старый таймер для user_id={user_id} завершён")
        del user["timeout_task"]
    # Если вопросы закончились, сохраняем результат
    if user["current_q"] >= len(user["questions"]):
        finish_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute("INSERT INTO results (user_id, score, test_date) VALUES (?, ?, ?)",
                   (user_id, user["score"], finish_time), commit=True)
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Начать тестирование")]], resize_keyboard=True)
        await message.answer(f"Тест окончен! Ваш результат: {user['score']}", reply_markup=kb)
        if ENABLE_LOGGING:
            logging.info(f"[TEST_END] user_id={user_id}, score={user['score']}")
        del user_data[user_id]
        return
    # Получаем очередной вопрос
    q_tuple = user["questions"][user["current_q"]]
    user["current_q"] += 1
    question_text = q_tuple[3]
    option1 = q_tuple[4]
    option2 = q_tuple[5]
    option3 = q_tuple[6]
    option4 = q_tuple[7]
    correct_idx = q_tuple[8]
    opts = [option1, option2, option3, option4]
    correct_ans = opts[correct_idx - 1]
    random.shuffle(opts)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=o)] for o in opts], resize_keyboard=True)
    user["current_options"] = opts
    user["current_correct"] = correct_ans
    user["answered"] = False
    await message.answer(f"{question_text}\nОсталось 30 секунд на ответ:", reply_markup=kb)
    # Запускаем обратный отсчёт и сохраняем задачу
    timeout_msg = await message.answer("Осталось 30 секунд...")
    user["timeout_task"] = asyncio.create_task(timeout_handler(message, user_id, q_tuple, timeout_msg))

##############################################################################
# Функция timeout_handler – обработка ситуации, когда время вышло
##############################################################################
async def timeout_handler(message: types.Message, user_id: int, q_tuple, timeout_msg: types.Message):
    # Запускаем обратный отсчёт через отдельную корутину
    await countdown_task(timeout_msg, 30)
    # Если время вышло и пользователь не ответил, фиксируем пропуск
    user = user_data.get(user_id)
    if not user or user.get("answered"):
        return
    category = q_tuple[1]
    question_text = q_tuple[3]
    db.execute("INSERT INTO user_answers (user_id, category, question, user_answer, is_correct) VALUES (?, ?, ?, ?, ?)",
               (user_id, category, question_text, "Время вышло", 0), commit=True)
    await message.answer("⏳ Время вышло! Ответ не засчитан.")
    if ENABLE_LOGGING:
        logging.info(f"[TIMEOUT] user_id={user_id}, вопрос='{question_text[:30]}...'")
    await ask_question(message, user_id)

##############################################################################
# Хендлер universal_text_handler – регистрация и обработка ответов
##############################################################################
@dp.message(lambda msg: msg.text and not msg.text.startswith("/"))
async def universal_text_handler(message: types.Message):
    user_id = message.from_user.id
    row = db.execute("SELECT name, role FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not row:
        step = user_data.get(user_id, {}).get("registration_step")
        if step == "ask_name":
            name = message.text.strip()
            user_data[user_id]["temp_name"] = name
            user_data[user_id]["registration_step"] = "ask_role"
            kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=r)] for r in AVAILABLE_ROLES], resize_keyboard=True)
            await message.answer("Выберите роль: qa, sa, dev_back, dev_front, other?", reply_markup=kb)
            if ENABLE_LOGGING:
                logging.info(f"[REGISTER] user_id={user_id}, имя: {name}, запрошена роль")
        elif step == "ask_role":
            role_inp = message.text.strip().lower()
            if role_inp not in AVAILABLE_ROLES:
                await message.answer("Нет такой роли. Доступны: qa, sa, dev_back, dev_front, other")
                return
            name = user_data[user_id]["temp_name"]
            db.execute("INSERT INTO users (user_id, name, role) VALUES (?, ?, ?)",
                       (user_id, name, role_inp), commit=True)
            user_data[user_id]["registration_step"] = None
            del user_data[user_id]["temp_name"]
            if ENABLE_LOGGING:
                logging.info(f"[REGISTER] user_id={user_id}, {name} зарегистрирован с ролью {role_inp}")
            kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Начать тестирование")]], resize_keyboard=True)
            await message.answer(f"Отлично, {name} (роль: {role_inp})! Можно начать тест.", reply_markup=kb)
        else:
            await message.answer("Сначала введите имя, затем роль (/start).")
    else:
        user = user_data.get(user_id)
        if user and "current_options" in user:
            if message.text in user["current_options"]:
                await check_answer(message)
                return
            else:
                await message.answer("Пожалуйста, используйте кнопки для выбора ответа.")
                return
        await message.answer("Неизвестная команда или вы не в процессе теста.")

##############################################################################
# Функция check_answer – обработка ответа пользователя
##############################################################################
async def check_answer(message: types.Message):
    user_id = message.from_user.id
    user = user_data.get(user_id)
    if not user or "current_options" not in user:
        return
    user["answered"] = True
    if "timeout_task" in user:
        task = user["timeout_task"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            if ENABLE_LOGGING:
                logging.info(f"[TIMER_CANCELLED] user_id={user_id} во время ответа")
        del user["timeout_task"]
    q_tuple = user["questions"][user["current_q"] - 1]
    chosen = message.text
    correct_ans = user["current_correct"]
    is_correct = 1 if chosen == correct_ans else 0
    category = q_tuple[1]
    question_text = q_tuple[3]
    db.execute("INSERT INTO user_answers (user_id, category, question, user_answer, is_correct) VALUES (?, ?, ?, ?, ?)",
               (user_id, category, question_text, chosen, is_correct), commit=True)
    if is_correct:
        user["score"] += 1
        await message.answer("Правильно! ✅ +1 балл")
    else:
        await message.answer(f"Неверно ❌ Правильный ответ: {correct_ans}")
    if ENABLE_LOGGING:
        logging.info(f"[ANSWER] user_id={user_id}, выбранный: '{chosen}', правильный: '{correct_ans}', результат: {is_correct}")
    await ask_question(message, user_id)

##############################################################################
# Точка входа – запуск бота
##############################################################################
if __name__ == "__main__":
    async def main():
        logging.info("Бот запущен...")
        await dp.start_polling(bot)
    asyncio.run(main())