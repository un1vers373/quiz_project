import random

from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

ROLE_ADMIN = 'admin'      # Администратор / Создатель викторин
ROLE_STUDENT = 'student'  # Студент

OPTION_CHOICES = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')]


def timer_grace():
    return getattr(settings, 'QUIZ_TIMER_GRACE_SECONDS', 5)


class AttemptNotAllowed(Exception):
    # Студент уже прошёл викторину и видел разбор ошибок - повторная попытка запрещена.
    pass


# ---------------------------------------------------------------- 1. UserProfile
class UserProfile(models.Model):
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Администратор/Создатель'),
        (ROLE_STUDENT, 'Студент'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField('Роль', max_length=10, choices=ROLE_CHOICES, default=ROLE_STUDENT)
    avatar = models.ImageField(
        'Аватар', upload_to='avatars/', blank=True,
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'gif', 'webp'])],
    )
    bio = models.TextField('О себе', max_length=300, blank=True)
    # Управление рейтингом (доступно администратору):
    in_rating = models.BooleanField('Показывать в рейтинге', default=True)
    rating_bonus = models.IntegerField('Корректировка баллов в рейтинге', default=0)
    email_verified = models.BooleanField('Почта подтверждена', default=False)

    class Meta:
        verbose_name = 'Профиль пользователя'
        verbose_name_plural = 'Профили пользователей'

    def __str__(self):
        return f'{self.user} ({self.get_role_display()})'


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
    # Каждому новому пользователю автоматически создаётся профиль.
    # Суперпользователь получает роль администратора, все остальные - студента.
    if created:
        UserProfile.objects.get_or_create(
            user=instance,
            defaults={'role': ROLE_ADMIN if instance.is_superuser else ROLE_STUDENT},
        )


def user_role(user):
    # Возвращает роль пользователя или None для анонимного пользователя.
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return ROLE_ADMIN
    if not hasattr(user, '_role_cache'):
        profile, _ = UserProfile.objects.get_or_create(user=user)
        user._role_cache = profile.role
    return user._role_cache


# ---------------------------------------------------------------------- 2. Quiz
class Quiz(models.Model):
    title = models.CharField('Название', max_length=200)
    description = models.TextField('Описание', blank=True)
    time_limit_minutes = models.PositiveIntegerField(
        'Лимит времени (мин.)', default=10,
        validators=[MinValueValidator(1), MaxValueValidator(180)],
    )
    is_published = models.BooleanField('Опубликована', default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quizzes')
    created_at = models.DateTimeField(auto_now_add=True)
    # Лайки (как в мессенджерах): один пользователь - один лайк, повторное нажатие снимает его.
    likes = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='liked_quizzes',
                                   blank=True, verbose_name='Лайки')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Викторина'
        verbose_name_plural = 'Викторины'

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('quiz-detail', args=[self.pk])

    @property
    def time_limit_seconds(self):
        return self.time_limit_minutes * 60

    @property
    def question_count(self):
        return len(self.questions.all())

    @property
    def total_points(self):
        return sum(q.points for q in self.questions.all())


# ------------------------------------------------------------------ 3. Question
class Question(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField('Текст вопроса')
    option_a = models.CharField('Вариант A', max_length=255)
    option_b = models.CharField('Вариант B', max_length=255)
    option_c = models.CharField('Вариант C', max_length=255)
    option_d = models.CharField('Вариант D', max_length=255)
    correct_option = models.CharField('Правильный ответ', max_length=1, choices=OPTION_CHOICES)
    points = models.PositiveIntegerField('Баллы', default=1, validators=[MinValueValidator(1)])

    class Meta:
        ordering = ['id']
        verbose_name = 'Вопрос'
        verbose_name_plural = 'Вопросы'

    def __str__(self):
        return self.text[:60]

    @property
    def options(self):
        return [('A', self.option_a), ('B', self.option_b), ('C', self.option_c), ('D', self.option_d)]


# -------------------------------------------------------------- 4. QuizAttempt
class QuizAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attempts')
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='attempts')
    started_at = models.DateTimeField('Старт', default=timezone.now)
    finished_at = models.DateTimeField('Финиш', null=True, blank=True)
    score = models.PositiveIntegerField('Баллы', default=0)
    is_valid = models.BooleanField('Засчитана', default=True)  # False - аннулирована по таймеру
    answers = models.JSONField('Ответы', default=dict, blank=True)  # {"<id вопроса>": "A"}
    # Отзыв о викторине хранится в попытке (так моделей остаётся ровно 4):
    # оставить его можно только после прохождения.
    review = models.TextField('Отзыв', max_length=500, blank=True)
    reviewed_at = models.DateTimeField('Дата отзыва', null=True, blank=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Попытка прохождения'
        verbose_name_plural = 'Попытки прохождения'

    def __str__(self):
        return f'{self.user} - {self.quiz} ({self.score})'

    # --- вспомогательные свойства
    @property
    def is_finished(self):
        return self.finished_at is not None

    @property
    def time_limit_seconds(self):
        return self.quiz.time_limit_seconds

    @property
    def max_score(self):
        return self.quiz.total_points

    @property
    def duration_seconds(self):
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    # --- логика таймера
    def remaining_seconds(self, now=None):
        now = now or timezone.now()
        elapsed = (now - self.started_at).total_seconds()
        return max(0, int(self.time_limit_seconds - elapsed))

    def is_expired(self, now=None):
        now = now or timezone.now()
        elapsed = (now - self.started_at).total_seconds()
        return elapsed > self.time_limit_seconds + timer_grace()

    # --- подсчёт баллов
    def calculate_score(self):
        total = 0
        for question in self.quiz.questions.all():
            if self.answers.get(str(question.pk)) == question.correct_option:
                total += question.points
        return total

    def wrong_answers(self):
        # Разбор ошибок: вопросы, на которые студент ответил неверно или не ответил.
        # Для аннулированной попытки разбор не показывается.
        if not self.is_valid or self.finished_at is None:
            return []
        result = []
        for question in self.quiz.questions.all():
            options = dict(question.options)
            given = self.answers.get(str(question.pk))
            if given != question.correct_option:
                result.append({
                    'question_id': question.pk,
                    'text': question.text,
                    'given': given,
                    'given_text': options.get(given),
                    'correct': question.correct_option,
                    'correct_text': options[question.correct_option],
                })
        return result

    def finish(self, answers, now=None):
        # Завершает попытку. Если время вышло за лимит (с допуском) - попытка
        # аннулируется (is_valid=False) и ставится 0 баллов.
        if self.finished_at is not None:
            raise ValueError('Попытка уже завершена.')
        now = now or timezone.now()
        self.finished_at = now
        self.answers = {str(k): v for k, v in answers.items() if v}
        if self.is_expired(now):
            self.is_valid = False
            self.score = 0
        else:
            self.is_valid = True
            self.score = self.calculate_score()
        self.save()
        return self

    @classmethod
    def start_for(cls, user, quiz):
        # Возвращает текущую незавершённую попытку или создаёт новую.
        # Просроченная незавершённая попытка аннулируется.
        # Одна засчитанная попытка на викторину: после неё студент уже видел
        # разбор ошибок, поэтому повторное прохождение запрещено.
        # (Аннулированная по таймеру попытка разбора не даёт, её можно пройти заново.)
        if cls.objects.filter(user=user, quiz=quiz, is_valid=True, finished_at__isnull=False).exists():
            raise AttemptNotAllowed(
                'Вы уже прошли эту викторину. Повторное прохождение недоступно: вы видели правильные ответы.')
        now = timezone.now()
        open_attempt = cls.objects.filter(user=user, quiz=quiz, finished_at__isnull=True).order_by('-started_at').first()
        if open_attempt is not None:
            if open_attempt.is_expired(now):
                open_attempt.finish({}, now=now)
            else:
                return open_attempt
        return cls.objects.create(user=user, quiz=quiz)


# --------------------------------------------------------------------- Рейтинг
def email_taken(email, exclude_user=None):
    # Почта занята, если её уже подтвердил другой пользователь.
    queryset = UserProfile.objects.filter(user__email__iexact=email, email_verified=True)
    if exclude_user is not None:
        queryset = queryset.exclude(user=exclude_user)
    return queryset.exists()


def base_score(user):
    # Сумма лучших засчитанных результатов игрока по каждой викторине (без корректировки).
    best = {}
    for attempt in QuizAttempt.objects.filter(user=user, is_valid=True, finished_at__isnull=False):
        best[attempt.quiz_id] = max(best.get(attempt.quiz_id, 0), attempt.score)
    return sum(best.values())


def get_leaderboard(quiz=None, limit=50):
    # Рейтинг: для каждого студента берётся лучшая засчитанная попытка по каждой
    # викторине, баллы суммируются. Администратор может убрать игрока из рейтинга
    # (in_rating=False) или скорректировать его общий счёт (rating_bonus; только в общем
    # рейтинге, не в рейтинге отдельной викторины). При равенстве выше тот, кто быстрее.
    attempts = (QuizAttempt.objects.filter(is_valid=True, finished_at__isnull=False)
                .exclude(user__profile__in_rating=False).select_related('user'))
    if quiz is not None:
        attempts = attempts.filter(quiz=quiz)

    best = {}
    for attempt in sorted(attempts, key=lambda a: (-a.score, a.duration_seconds)):
        best.setdefault((attempt.user_id, attempt.quiz_id), attempt)

    totals = {}
    for (user_id, _quiz_id), attempt in best.items():
        row = totals.setdefault(user_id, {
            'user_id': user_id,
            'username': attempt.user.username,
            'base_score': 0,
            'total_time': 0.0,
            'quizzes_done': 0,
        })
        row['base_score'] += attempt.score
        row['total_time'] += attempt.duration_seconds
        row['quizzes_done'] += 1

    profiles = {p.user_id: p for p in UserProfile.objects.filter(user_id__in=list(totals))}
    for user_id, row in totals.items():
        profile = profiles.get(user_id)
        row['bonus'] = profile.rating_bonus if (profile and quiz is None) else 0
        row['total_score'] = row['base_score'] + row['bonus']
        row['avatar'] = profile.avatar.url if (profile and profile.avatar) else ""

    rows = sorted(totals.values(), key=lambda r: (-r['total_score'], r['total_time']))[:limit]
    for position, row in enumerate(rows, start=1):
        row['rank'] = position
        row['total_time'] = round(row['total_time'], 1)
    return rows


# ----------------------------------------------------- Банк случайных вопросов
# Формат: (текст вопроса, правильный ответ, [три неправильных], баллы)
QUESTION_BANK = {
    'programming': [
        ('Какой оператор в Python возводит число в степень?', '**', ['^', '//', '%'], 1),
        ('Что выведет print(len([1, 2, 3]))?', '3', ['1', '2', '4'], 1),
        ('Какой тип данных в Python хранит пары «ключ: значение»?', 'dict', ['list', 'tuple', 'set'], 1),
        ('Что означает аббревиатура HTML?', 'HyperText Markup Language',
         ['High Transfer Machine Language', 'Home Tool Markup Language', 'Hyperlinks and Text Mark Language'], 1),
        ('Какой SQL-оператор выбирает данные из таблицы?', 'SELECT', ['GET', 'FETCH', 'OPEN'], 1),
        ('Что делает команда git commit?', 'Сохраняет изменения в локальный репозиторий',
         ['Отправляет код на сервер', 'Скачивает репозиторий', 'Удаляет ветку'], 1),
        ('Какое ключевое слово создаёт функцию в Python?', 'def', ['func', 'function', 'fn'], 1),
        ('Что означает HTTP-код 404?', 'Страница не найдена',
         ['Успешный запрос', 'Ошибка сервера', 'Доступ запрещён'], 1),
        ('Чему равно 10 // 3 в Python?', '3', ['3.33', '4', '1'], 2),
        ('Что такое Django?', 'Веб-фреймворк на Python',
         ['Язык программирования', 'База данных', 'Текстовый редактор'], 1),
        ('Что вернёт bool("") в Python?', 'False', ['True', 'None', 'Ошибку'], 2),
        ('Какая структура данных работает по принципу LIFO?', 'Стек',
         ['Очередь', 'Хеш-таблица', 'Двоичное дерево'], 2),
        ('Какова сложность бинарного поиска?', 'O(log n)', ['O(1)', 'O(n)', 'O(n^2)'], 2),
        ('Какой символ начинает однострочный комментарий в Python?', '#', ['//', '--', '/*'], 1),
        ('Что делает команда python manage.py migrate?', 'Применяет миграции к базе данных',
         ['Запускает сервер', 'Создаёт суперпользователя', 'Запускает тесты'], 1),
        ('Какой тег защищает формы Django от CSRF-атак?', 'csrf_token', ['secure', 'protect', 'token'], 1),
        ('Какой HTTP-статус означает «доступ запрещён»?', '403', ['200', '404', '500'], 1),
        ('Какая библиотека добавляет REST API в Django?', 'Django REST Framework',
         ['Flask', 'Pandas', 'NumPy'], 1),
    ],
    'logic': [
        ('Продолжите ряд: 2, 4, 8, 16, ...', '32', ['24', '30', '64'], 1),
        ('Продолжите ряд: 1, 1, 2, 3, 5, 8, ...', '13', ['11', '12', '15'], 2),
        ('Продолжите ряд: 1, 4, 9, 16, ...', '25', ['20', '24', '36'], 1),
        ('Продолжите ряд: 100, 90, 80, 70, ...', '60', ['65', '50', '40'], 1),
        ('У фермера было 17 овец. Все, кроме 9, убежали. Сколько овец осталось?', '9', ['8', '17', '0'], 2),
        ('Что тяжелее: килограмм ваты или килограмм железа?', 'Одинаково',
         ['Вата', 'Железо', 'Зависит от температуры'], 1),
        ('Сколько месяцев в году имеют не менее 28 дней?', '12', ['1', '2', '6'], 2),
        ('Три курицы за три дня снесли три яйца. Сколько яиц снесут 12 куриц за 12 дней?', '48',
         ['12', '24', '36'], 2),
        ('Какое число лишнее: 2, 4, 6, 9, 10?', '9', ['2', '6', '10'], 1),
        ('Какое слово лишнее: яблоко, груша, морковь, слива?', 'морковь', ['яблоко', 'груша', 'слива'], 1),
        ('Сколько будет 5 + 5 * 5?', '30', ['50', '55', '25'], 1),
        ('Если A > B и B > C, то какое утверждение верно?', 'A > C', ['C > A', 'A = C', 'A < C'], 1),
        ('Продолжите ряд букв: А, В, Д, Ж, ...', 'И', ['К', 'З', 'Й'], 2),
        ('Истинно ли выражение (2 > 1) И (3 < 2)?', 'Ложно', ['Истинно', 'Неопределено', 'Ошибка'], 1),
        ('Сколько получится, если разделить 30 на половину и прибавить 10?', '70', ['25', '15', '40'], 2),
        ('Продолжите ряд: 3, 6, 9, 12, ...', '15', ['14', '16', '18'], 1),
    ],
}

CATEGORY_INFO = {
    'programming': ('Программирование', 6),
    'logic': ('Логика', 8),
}


def create_random_quiz(category, creator, count=8):
    # Собирает и публикует викторину из случайных вопросов выбранной категории.
    title_base, minutes = CATEGORY_INFO[category]
    pool = QUESTION_BANK[category]
    chosen = random.sample(pool, min(count, len(pool)))
    number = Quiz.objects.filter(title__startswith=title_base + ': случайный набор').count() + 1
    quiz = Quiz.objects.create(
        title=f'{title_base}: случайный набор №{number}',
        description=f'Автоматически собранная викторина ({title_base.lower()}): {len(chosen)} случайных вопросов.',
        time_limit_minutes=minutes,
        is_published=True,
        created_by=creator,
    )
    for text, right, wrong, points in chosen:
        options = [right] + list(wrong)
        random.shuffle(options)  # порядок вариантов случайный, буква верного ответа пересчитывается
        Question.objects.create(
            quiz=quiz, text=text,
            option_a=options[0], option_b=options[1], option_c=options[2], option_d=options[3],
            correct_option='ABCD'[options.index(right)], points=points,
        )
    return quiz
