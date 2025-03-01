import csv
from db.database import Database

# Создаем объект базы данных
db = Database()

# Открываем CSV-файл с вопросами (убедитесь, что файл в кодировке UTF-8)
with open("questions.csv", newline='', encoding="utf-8") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        # Преобразуем числовые поля в int (если они указаны как числа)
        correct_option = int(row["correct_option"])
        difficulty = int(row["difficulty"])
        # Выполняем запрос вставки в таблицу questions
        db.execute("""
            INSERT INTO questions (category, difficulty, question, option1, option2, option3, option4, correct_option, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (row["category"], difficulty, row["question"], row["option1"], row["option2"], row["option3"], row["option4"], correct_option, row["role"]), commit=True)

print("Вопросы успешно загружены из файла.")