import os

BASE = '/home/claude/work/build/quiz_project'
PKG = BASE + '/quiz_platform'
SCRIPT = '/mnt/user-data/outputs/build_quiz_platform.py'


def read(path):
    return open(path, encoding='utf-8').read()


def write(path, text):
    open(path, 'w', encoding='utf-8', newline='\n').write(text)


def rep(path, old, new):
    s = read(path)
    assert s.count(old) == 1, (path, old[:70], s.count(old))
    write(path, s.replace(old, new))


# ============================================================ settings.py
rep(PKG + '/settings.py',
    "STATIC_URL = 'static/'\n",
    "STATIC_URL = 'static/'\n\n# Загружаемые файлы (аватарки профилей)\nMEDIA_URL = 'media/'\nMEDIA_ROOT = BASE_DIR / 'media'\n")

# ============================================================== models.py
M = PKG + '/models.py'
rep(M, "from django.core.validators import MaxValueValidator, MinValueValidator",
    "from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator")

rep(M, "def timer_grace():\n    return getattr(settings, 'QUIZ_TIMER_GRACE_SECONDS', 5)\n",
    """def timer_grace():
    return getattr(settings, 'QUIZ_TIMER_GRACE_SECONDS', 5)


class AttemptNotAllowed(Exception):
    # Студент уже прошёл викторину и видел разбор ошибок - повторная попытка запрещена.
    pass
""")

rep(M, "    role = models.CharField('Роль', max_length=10, choices=ROLE_CHOICES, default=ROLE_STUDENT)\n",
    """    role = models.CharField('Роль', max_length=10, choices=ROLE_CHOICES, default=ROLE_STUDENT)
    avatar = models.ImageField(
        'Аватар', upload_to='avatars/', blank=True,
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'gif', 'webp'])],
    )
    bio = models.TextField('О себе', max_length=300, blank=True)
""")

rep(M, "        return total\n\n    def finish(self, answers, now=None):",
    """        return total

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

    def finish(self, answers, now=None):""")

rep(M, "        now = timezone.now()\n        open_attempt = cls.objects.filter(",
    """        # Одна засчитанная попытка на викторину: после неё студент уже видел
        # разбор ошибок, поэтому повторное прохождение запрещено.
        # (Аннулированная по таймеру попытка разбора не даёт, её можно пройти заново.)
        if cls.objects.filter(user=user, quiz=quiz, is_valid=True, finished_at__isnull=False).exists():
            raise AttemptNotAllowed(
                'Вы уже прошли эту викторину. Повторное прохождение недоступно: вы видели правильные ответы.')
        now = timezone.now()
        open_attempt = cls.objects.filter(""")

# ============================================================== forms.py
F = PKG + '/forms.py'
rep(F, "from .models import Question, Quiz\n", "from .models import Question, Quiz, UserProfile\n")
write(F, read(F) + """

class ProfileForm(forms.ModelForm):
    # Профиль: аватарка и описание.
    MAX_AVATAR_SIZE = 2 * 1024 * 1024

    class Meta:
        model = UserProfile
        fields = ['avatar', 'bio']
        widgets = {'bio': forms.Textarea(attrs={'rows': 4, 'maxlength': 300})}

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        if avatar and getattr(avatar, 'size', 0) > self.MAX_AVATAR_SIZE:
            raise ValidationError('Файл слишком большой: максимум 2 МБ.')
        return avatar
""")

# ======================================================== serializers.py
S = PKG + '/serializers.py'
rep(S, "    detail = serializers.SerializerMethodField()\n",
    "    detail = serializers.SerializerMethodField()\n    wrong_answers = serializers.SerializerMethodField()\n")
rep(S, "                  'finished_at', 'duration_seconds', 'detail']",
    "                  'finished_at', 'duration_seconds', 'detail', 'wrong_answers']")
rep(S, "        return 'Попытка аннулирована: превышен лимит времени. Начислено 0 баллов.'\n",
    """        return 'Попытка аннулирована: превышен лимит времени. Начислено 0 баллов.'

    def get_wrong_answers(self, obj):
        # Разбор ошибок: вопрос, ответ студента и правильный вариант.
        return [
            {'question_id': w['question_id'], 'text': w['text'],
             'your_answer': w['given'], 'correct_answer': w['correct']}
            for w in obj.wrong_answers()
        ]
""")

# ============================================================== views.py
V = PKG + '/views.py'
rep(V, "from django.contrib import admin, messages\nfrom django.contrib.auth import login\n",
    "from django.conf import settings\nfrom django.conf.urls.static import static\n"
    "from django.contrib import admin, messages\nfrom django.contrib.auth import get_user_model, login\n")
rep(V, "from .forms import AttemptForm, QuestionForm, QuizForm",
    "from .forms import AttemptForm, ProfileForm, QuestionForm, QuizForm")
rep(V, "from .models import (ROLE_ADMIN, ROLE_STUDENT, Question, Quiz, QuizAttempt, UserProfile,",
    "from .models import (ROLE_ADMIN, ROLE_STUDENT, AttemptNotAllowed, Question, Quiz, QuizAttempt, UserProfile,")

rep(V, """def role_context(request):
    # Контекст-процессор: роли доступны во всех шаблонах.
    return {'is_creator': is_creator(request.user), 'is_student': is_student(request.user)}
""", """def role_context(request):
    # Контекст-процессор: роли и профиль текущего пользователя доступны во всех шаблонах.
    user = request.user
    profile = UserProfile.objects.get_or_create(user=user)[0] if user.is_authenticated else None
    return {'is_creator': is_creator(user), 'is_student': is_student(user), 'my_profile': profile}
""")

rep(V, "        return visible_quizzes(self.request.user)\n\n\nclass QuizDetailView(LoginRequiredMixin, DetailView):",
    """        return visible_quizzes(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        quizzes = list(context['quizzes'])
        if is_student(self.request.user):
            # Для студента отмечаем викторины, которые он уже прошёл.
            done = {a.quiz_id: a for a in QuizAttempt.objects.filter(
                user=self.request.user, is_valid=True, finished_at__isnull=False)}
            for quiz in quizzes:
                quiz.my_attempt = done.get(quiz.pk)
        context['quizzes'] = quizzes
        return context


class QuizDetailView(LoginRequiredMixin, DetailView):""")

rep(V, "            context['attempts'] = QuizAttempt.objects.filter(user=self.request.user, quiz=self.object)\n",
    """            attempts = QuizAttempt.objects.filter(user=self.request.user, quiz=self.object)
            context['attempts'] = attempts
            context['completed'] = attempts.filter(is_valid=True, finished_at__isnull=False).first()
""")

rep(V, "    attempt = QuizAttempt.start_for(request.user, quiz)\n    return redirect('attempt-take', pk=attempt.pk)\n",
    """    try:
        attempt = QuizAttempt.start_for(request.user, quiz)
    except AttemptNotAllowed as exc:
        messages.error(request, str(exc))
        done = QuizAttempt.objects.filter(user=request.user, quiz=quiz, is_valid=True,
                                          finished_at__isnull=False).first()
        return redirect('attempt-result', pk=done.pk) if done else redirect(quiz)
    return redirect('attempt-take', pk=attempt.pk)
""")

rep(V, "    return render(request, TEMPLATE, {'page': 'result', 'attempt': attempt})\n",
    "    context = {'page': 'result', 'attempt': attempt, 'review': attempt.wrong_answers()}\n"
    "    return render(request, TEMPLATE, context)\n")

rep(V, "@login_required\ndef leaderboard(request):", '''@login_required
def profile(request, username=None):
    # Профиль: свой (с формой редактирования аватарки и описания) или чужой (только просмотр).
    target = request.user if username is None else get_object_or_404(get_user_model(), username=username)
    is_own = target.pk == request.user.pk
    profile_obj, _ = UserProfile.objects.get_or_create(user=target)

    form = None
    if is_own:
        if request.method == 'POST':
            form = ProfileForm(request.POST, request.FILES, instance=profile_obj)
            if form.is_valid():
                form.save()
                messages.success(request, 'Профиль обновлён.')
                return redirect('profile')
        else:
            form = ProfileForm(instance=profile_obj)

    results = QuizAttempt.objects.filter(
        user=target, is_valid=True, finished_at__isnull=False).select_related('quiz')
    context = {
        'page': 'profile',
        'profile_user': target,
        'profile_obj': profile_obj,
        'profile_role': user_role(target),
        'is_own': is_own,
        'form': form,
        'results': results,
        'total_score': sum(a.score for a in results),
    }
    return render(request, TEMPLATE, context)


@login_required
def leaderboard(request):''')

rep(V, "        attempt = QuizAttempt.start_for(request.user, quiz)\n        return Response(AttemptStartSerializer(attempt).data, status=status.HTTP_201_CREATED)",
    """        try:
            attempt = QuizAttempt.start_for(request.user, quiz)
        except AttemptNotAllowed as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(AttemptStartSerializer(attempt).data, status=status.HTTP_201_CREATED)""")

rep(V, "    path('leaderboard/', leaderboard, name='leaderboard'),\n",
    "    path('leaderboard/', leaderboard, name='leaderboard'),\n"
    "    path('profile/', profile, name='profile'),\n"
    "    path('profile/<str:username>/', profile, name='profile-user'),\n")

rep(V, "    path('api/leaderboard/', LeaderboardAPIView.as_view(), name='api-leaderboard'),\n]\n",
    "    path('api/leaderboard/', LeaderboardAPIView.as_view(), name='api-leaderboard'),\n]\n\n"
    "# Аватарки лежат в MEDIA_ROOT; в режиме DEBUG их раздаёт сам Django.\n"
    "urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)\n")

# ============================================================== tests.py
T = PKG + '/tests.py'
rep(T, "from .models import (ROLE_ADMIN, ROLE_STUDENT, Question, Quiz, QuizAttempt, UserProfile,",
    "from .models import (ROLE_ADMIN, ROLE_STUDENT, AttemptNotAllowed, Question, Quiz, QuizAttempt, UserProfile,")

t = read(T)
a = t.index("    # 3. Логика проверки таймера\n")
b = t.index("    # 4. ")
new3 = """    # 3. Логика проверки таймера и правило одной попытки
    def test_timer_validation(self):
        answers = {str(self.q1.pk): 'B', str(self.q2.pk): 'A'}

        # Просрочка: попытка аннулируется, 0 баллов, разбор ошибок не показывается
        late = QuizAttempt.start_for(self.alice, self.quiz)
        late.finish(answers, now=late.started_at + timedelta(minutes=10, seconds=30))
        self.assertFalse(late.is_valid)
        self.assertEqual(late.score, 0)
        self.assertEqual(late.wrong_answers(), [])
        with self.assertRaises(ValueError):
            late.finish(answers)

        # После аннулирования можно пройти заново и уложиться во время
        on_time = QuizAttempt.start_for(self.alice, self.quiz)
        on_time.finish(answers, now=on_time.started_at + timedelta(minutes=5))
        self.assertTrue(on_time.is_valid)
        self.assertEqual(on_time.score, 5)

        # Засчитанная попытка закрывает повторное прохождение
        with self.assertRaises(AttemptNotAllowed):
            QuizAttempt.start_for(self.alice, self.quiz)

"""
t = t[:a] + new3 + t[b:]
write(T, t)

rep(T, "        self.assertContains(page, 'alice')\n        self.assertContains(page, 'bob')\n",
    """        self.assertContains(page, 'alice')
        self.assertContains(page, 'bob')

        # Разбор ошибок: bob ошибся во втором вопросе - ему показывают верный ответ,
        # а повторный старт викторины запрещён
        self.client.logout()
        self.client.login(username='bob', password=PASSWORD)
        bob_attempt = QuizAttempt.objects.get(user=self.bob, quiz=self.quiz)
        result = self.client.get(reverse('attempt-result', args=[bob_attempt.pk]))
        self.assertContains(result, 'Правильный ответ')
        self.assertContains(result, 'Бишкек')
        self.client.post(reverse('quiz-start', args=[self.quiz.pk]))
        self.assertEqual(QuizAttempt.objects.filter(user=self.bob, quiz=self.quiz).count(), 1)
""")

# ======================================================= requirements/README
rep(BASE + '/requirements.txt', "djangorestframework>=3.15,<4.0\n", "djangorestframework>=3.15,<4.0\nPillow>=10.0\n")
rep(BASE + '/README.md',
    "по программированию или логике (банк вопросов находится в `models.py`).",
    """по программированию или логике (банк вопросов находится в `models.py`).

## Правила прохождения

- Каждую викторину студент проходит **один раз**. После завершения он видит разбор ошибок
  (его ответ и правильный вариант), а повторный запуск этой викторины блокируется.
- Если попытка аннулирована по таймеру (0 баллов), разбор ошибок не показывается и викторину можно пройти заново.
- Викторины на главной страницы показаны горизонтальной лентой (стрелки или прокрутка).

## Профиль

Ссылка с аватаркой в правом верхнем углу ведёт в профиль. Там можно загрузить аватарку
(JPG, PNG, GIF, WEBP до 2 МБ) и написать о себе. Профили других студентов открываются по клику на имя в рейтинге.
Для аватарок нужна библиотека Pillow (есть в `requirements.txt`), файлы сохраняются в папку `media/`.

После обновления проекта выполните `pip install -r requirements.txt`, затем
`python manage.py makemigrations quiz_platform` и `python manage.py migrate`.""")

# ============================================================= app.html
H = BASE + '/templates/app.html'

rep(H, ".card.bad .score { color: var(--bad); }\n</style>", """.card.bad .score { color: var(--bad); }

/* лента викторин: слева направо */
.feed-wrap { position: relative; }
.feed {
  display: flex; gap: 16px; overflow-x: auto; scroll-snap-type: x mandatory; padding: 6px 2px 18px;
  scrollbar-width: thin; scrollbar-color: var(--panel-2) transparent;
}
.slide { flex: 0 0 290px; scroll-snap-align: start; margin: 0; min-height: 230px; display: flex; flex-direction: column; justify-content: space-between; }
.feed-arrow {
  position: absolute; top: 50%; transform: translateY(-60%); z-index: 3; width: 44px; height: 44px; padding: 0;
  border-radius: 50%; font-size: 1.7rem; line-height: 1; background: var(--bg-dark); color: var(--ink); border: 1px solid var(--line);
}
.feed-arrow:hover { background: var(--green); color: #0b0c10; }
.feed-arrow.left { left: -16px; } .feed-arrow.right { right: -16px; }
@media (hover: none) { .feed-arrow { display: none; } }
.chip.done { background: #23352b; color: var(--green); }

/* профиль и аватарки */
.me-chip { display: flex; align-items: center; gap: 8px; color: var(--ink); background: var(--panel); border-radius: 30px; padding: 4px 14px 4px 4px; }
.me-chip:hover { text-decoration: none; background: var(--panel-2); }
.me-chip .role { background: var(--bg-dark); }
.avatar { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; flex: none; }
.avatar.ph { display: inline-flex; align-items: center; justify-content: center; background: var(--green); color: #0b0c10; font-weight: 800; }
.avatar.xl { width: 120px; height: 120px; font-size: 3rem; }
.profile-head { display: flex; flex-wrap: wrap; gap: 24px; align-items: center; margin-bottom: 8px; }
.profile-head h1 { margin: 0 0 6px; } .bio { max-width: 520px; }
input[type=file] { color: var(--muted); max-width: 100%; }

/* разбор ошибок и заметки */
.card.wrong { border-left: 4px solid var(--bad); }
.given { color: var(--bad); margin: 6px 0 2px; } .right { color: var(--green); margin: 2px 0; font-weight: 600; }
.note { background: var(--panel); border-left: 4px solid var(--gold); border-radius: 6px; padding: 12px 16px; margin: 14px 0; }
</style>""")

rep(H, "      <span class=\"role\">{{ user.username }} · {% if is_creator %}Администратор{% else %}Студент{% endif %}</span>\n",
    """      <a class="me-chip" href="{% url 'profile' %}" title="Мой профиль">
        {% if my_profile and my_profile.avatar %}<img class="avatar" src="{{ my_profile.avatar.url }}" alt="">{% else %}<span class="avatar ph">{{ user.username|slice:":1"|upper }}</span>{% endif %}
        <span>{{ user.username }}</span>
        <span class="role">{% if is_creator %}Администратор{% else %}Студент{% endif %}</span>
      </a>
""")

# список викторин -> лента
h = read(H)
a = h.index('  <div class="grid">')
b = h.index("{% elif page == 'quiz_detail' %}")
feed = """  <div class="feed-wrap">
    <button type="button" class="feed-arrow left" id="feed-prev" aria-label="Назад">‹</button>
    <div class="feed" id="feed">
      {% for quiz in quizzes %}
        <div class="card slide">
          <div>
            <h3><a href="{{ quiz.get_absolute_url }}">{{ quiz.title }}</a>{% if not quiz.is_published %} <span class="tag">черновик</span>{% endif %}</h3>
            <p class="muted">{{ quiz.description|truncatechars:110 }}</p>
            <div class="chips">
              <span class="chip">⏱ {{ quiz.time_limit_minutes }} мин</span>
              <span class="chip">❓ {{ quiz.question_count }}</span>
              <span class="chip">⭐ {{ quiz.total_points }} б.</span>
              {% if quiz.my_attempt %}<span class="chip done">✅ {{ quiz.my_attempt.score }} / {{ quiz.total_points }}</span>{% endif %}
            </div>
          </div>
          {% if is_creator %}
            <div class="actions">
              <a href="{% url 'quiz-update' quiz.pk %}">Редактировать</a>
              <a href="{% url 'question-add' quiz.pk %}">+ Вопрос</a>
              <a href="{% url 'quiz-delete' quiz.pk %}">Удалить</a>
            </div>
          {% elif quiz.my_attempt %}
            <div class="actions"><a class="btn alt" href="{% url 'attempt-result' quiz.my_attempt.pk %}">Мой результат</a></div>
          {% else %}
            <div class="actions"><a class="btn" href="{{ quiz.get_absolute_url }}">Открыть</a></div>
          {% endif %}
        </div>
      {% empty %}
        <p class="muted">Пока нет доступных викторин.</p>
      {% endfor %}
    </div>
    <button type="button" class="feed-arrow right" id="feed-next" aria-label="Вперёд">›</button>
  </div>
  <script>
  (function () {
    var feed = document.getElementById('feed');
    if (!feed) { return; }
    function step() { return Math.max(300, feed.clientWidth * 0.8); }
    document.getElementById('feed-prev').addEventListener('click', function () { feed.scrollBy({ left: -step(), behavior: 'smooth' }); });
    document.getElementById('feed-next').addEventListener('click', function () { feed.scrollBy({ left: step(), behavior: 'smooth' }); });
  })();
  </script>

"""
write(H, h[:a] + feed + h[b:])

# страница викторины: одна попытка
rep(H, """    <form method="post" action="{% url 'quiz-start' quiz.pk %}">{% csrf_token %}<button>▶ Начать викторину</button></form>
""", """    {% if completed %}
      <div class="note">✅ Вы уже прошли эту викторину: <b>{{ completed.score }} / {{ quiz.total_points }}</b>. Повторное прохождение недоступно, потому что вы видели правильные ответы.</div>
      <a class="btn" href="{% url 'attempt-result' completed.pk %}">Результат и разбор ошибок</a>
    {% else %}
      <div class="note">☝ Засчитывается только одна попытка. После завершения вы увидите свои ошибки и правильные ответы, и пройти викторину ещё раз будет нельзя.</div>
      <form method="post" action="{% url 'quiz-start' quiz.pk %}" onsubmit="return confirm('Начать викторину? Попытка будет одна.');">{% csrf_token %}<button>▶ Начать викторину</button></form>
    {% endif %}
""")

# результат: разбор ошибок
rep(H, """  <p>
    <a class="btn" href="{% url 'leaderboard' %}">🏆 Рейтинг</a>
    <a class="btn alt" href="{{ attempt.quiz.get_absolute_url }}">Ещё раз</a>
    <a class="btn alt" href="{% url 'quiz-list' %}">Все викторины</a>
  </p>
""", """  {% if attempt.is_valid %}
    <h2>Разбор ошибок</h2>
    {% for w in review %}
      <div class="card wrong">
        <b>{{ forloop.counter }}. {{ w.text }}</b>
        <p class="given">✗ Ваш ответ: {% if w.given %}{{ w.given }}) {{ w.given_text }}{% else %}нет ответа{% endif %}</p>
        <p class="right">✓ Правильный ответ: {{ w.correct }}) {{ w.correct_text }}</p>
      </div>
    {% empty %}
      <div class="card">🎉 Ошибок нет: все ответы верные!</div>
    {% endfor %}
    {% if attempt.user_id == user.id %}
      <p class="muted">Повторно пройти эту викторину нельзя: вы уже видели правильные ответы.</p>
    {% endif %}
  {% endif %}
  <p>
    <a class="btn" href="{% url 'leaderboard' %}">🏆 Рейтинг</a>
    {% if not attempt.is_valid %}<a class="btn alt" href="{{ attempt.quiz.get_absolute_url }}">Попробовать снова</a>{% endif %}
    <a class="btn alt" href="{% url 'quiz-list' %}">Все викторины</a>
  </p>
""")

# рейтинг: ссылка на профиль
rep(H, "<td>{{ e.username }}</td>", """<td><a href="{% url 'profile-user' e.username %}">{{ e.username }}</a></td>""")

# страница профиля
rep(H, "  </table>\n{% endif %}\n</main>", """  </table>

{% elif page == 'profile' %}
  <section class="profile-head">
    {% if profile_obj.avatar %}<img class="avatar xl" src="{{ profile_obj.avatar.url }}" alt="">{% else %}<span class="avatar xl ph">{{ profile_user.username|slice:":1"|upper }}</span>{% endif %}
    <div>
      <h1>{{ profile_user.username }}</h1>
      <span class="role">{% if profile_role == 'admin' %}Администратор{% else %}Студент{% endif %}</span>
      <p class="muted bio">{% if profile_obj.bio %}{{ profile_obj.bio|linebreaksbr }}{% elif is_own %}Расскажите о себе в форме ниже.{% else %}Пользователь пока ничего не рассказал о себе.{% endif %}</p>
    </div>
  </section>
  <div class="chips">
    <span class="chip">✅ Пройдено викторин: {{ results|length }}</span>
    <span class="chip">⭐ Всего баллов: {{ total_score }}</span>
  </div>
  {% if is_own %}
    <h2>Редактировать профиль</h2>
    <form method="post" enctype="multipart/form-data" class="panel">
      {% csrf_token %}
      {{ form.as_p }}
      <button>Сохранить</button>
    </form>
  {% endif %}
  <h2>Результаты</h2>
  {% if results %}
    <table>
      <tr><th>Викторина</th><th>Баллы</th><th>Дата</th></tr>
      {% for a in results %}
        <tr><td>{{ a.quiz.title }}</td><td>{{ a.score }} / {{ a.max_score }}</td><td>{{ a.finished_at|date:"d.m.Y H:i" }}</td></tr>
      {% endfor %}
    </table>
  {% else %}
    <p class="muted">Пока нет пройденных викторин.</p>
  {% endif %}
{% endif %}
</main>""")

# ================================================ пересборка build-скрипта
order = ['manage.py', 'quiz_platform/__init__.py', 'quiz_platform/settings.py', 'quiz_platform/models.py',
         'quiz_platform/forms.py', 'quiz_platform/serializers.py', 'quiz_platform/views.py',
         'quiz_platform/tests.py', 'templates/app.html', 'requirements.txt', 'README.md']
src = read(SCRIPT)
head = src[:src.index("# ---------------------------------------------------------------- manage.py")]
foot = src[src.index("\n\ndef build():"):]
parts = []
for rel in order:
    content = read(os.path.join(BASE, *rel.split('/')))
    assert "'''" not in content, rel
    if content == '':
        parts.append("FILES['%s'] = ''\n" % rel)
    else:
        parts.append("FILES['%s'] = r'''%s'''\n" % (rel, content))
write(SCRIPT, head + "\n".join(parts) + foot)
print('ok')