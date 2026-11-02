import logging

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin, messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView
from rest_framework import permissions, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from .forms import (AttemptForm, EmailForm, LoginForm, ProfileForm, QuestionForm, QuizForm,
                    RegisterForm, ReviewForm)
from .models import (ROLE_ADMIN, ROLE_STUDENT, AttemptNotAllowed, Question, Quiz, QuizAttempt, UserProfile,
                     QUESTION_BANK, base_score, create_random_quiz, email_taken, get_leaderboard, user_role)
from .serializers import (AnswerSubmitSerializer, AttemptResultSerializer, AttemptStartSerializer,
                          LeaderboardEntrySerializer, QuizSerializer)

logger = logging.getLogger(__name__)
TEMPLATE = 'app.html'
VERIFY_SALT = 'email-verify'

# Модели доступны в админ-панели /admin/ (там назначаются роли пользователям).
admin.site.register([UserProfile, Quiz, Question, QuizAttempt])


# ======================================================================
# Роли и права доступа
# ======================================================================
def is_creator(user):
    # Администратор/Создатель: полный CRUD викторин.
    return user_role(user) == ROLE_ADMIN


def is_student(user):
    # Студент: прохождение викторин и просмотр рейтинга.
    return user_role(user) == ROLE_STUDENT


def role_context(request):
    # Контекст-процессор: роли и профиль текущего пользователя доступны во всех шаблонах.
    user = request.user
    profile = UserProfile.objects.get_or_create(user=user)[0] if user.is_authenticated else None
    return {'is_creator': is_creator(user), 'is_student': is_student(user), 'my_profile': profile}


class CreatorRequiredMixin(UserPassesTestMixin):
    # Аноним -> редирект на вход, студент -> 403.
    def test_func(self):
        return is_creator(self.request.user)


def quiz_reviews(quiz):
    # Отзывы о викторине (свежие первыми): они хранятся в засчитанных попытках.
    return (QuizAttempt.objects
            .filter(quiz=quiz, is_valid=True, finished_at__isnull=False)
            .exclude(review="")
            .select_related('user', 'user__profile')
            .order_by('-reviewed_at')[:50])


def _back(request, default):
    # Безопасный возврат на страницу, с которой пришёл запрос (скрытое поле next).
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return default


def visible_quizzes(user):
    queryset = (Quiz.objects.select_related('created_by').prefetch_related('questions')
                .annotate(like_total=Count('likes', distinct=True)))
    return queryset if is_creator(user) else queryset.filter(is_published=True)


# ======================================================================
# Регистрация
# ======================================================================
def send_verification_email(request, user):
    # Письмо со ссылкой подтверждения. В ссылке подписанный токен (id пользователя и адрес),
    # подделать его нельзя; срок действия задаёт EMAIL_VERIFICATION_DAYS.
    token = signing.dumps({'uid': user.pk, 'email': user.email}, salt=VERIFY_SALT)
    link = request.build_absolute_uri(reverse('verify-email', args=[token]))
    days = getattr(settings, 'EMAIL_VERIFICATION_DAYS', 3)
    send_mail(
        'Подтверждение почты: Платформа викторин',
        f'Здравствуйте, {user.username}!\n\n'
        f'Чтобы подтвердить почту, перейдите по ссылке (действует {days} дн.):\n{link}\n\n'
        'Если вы ничего не делали на нашем сайте, просто проигнорируйте это письмо.',
        None,
        [user.email],
    )


def try_send_verification(request, user):
    try:
        send_verification_email(request, user)
    except Exception:
        logger.exception('Не удалось отправить письмо подтверждения на %s', user.email)
        return False
    return True


def register(request):
    # Регистрация по почте. Аккаунт неактивен, пока студент не перейдёт по ссылке из письма:
    # так мы убеждаемся, что адрес настоящий и принадлежит ему.
    # Администраторов назначает суперпользователь в /admin/.
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.is_active = False
        user.save()
        sent = try_send_verification(request, user)
        return render(request, TEMPLATE, {'page': 'verify_sent', 'email': user.email, 'sent': sent})
    return render(request, TEMPLATE, {'page': 'register', 'form': form})


def verify_email(request, token):
    days = getattr(settings, 'EMAIL_VERIFICATION_DAYS', 3)
    where = 'profile' if request.user.is_authenticated else 'login'
    user_model = get_user_model()
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=days * 86400)
        user = user_model.objects.get(pk=data['uid'])
    except (signing.BadSignature, user_model.DoesNotExist, KeyError, TypeError):
        messages.error(request, 'Ссылка недействительна или устарела. Запросите письмо ещё раз.')
        return redirect(where)
    if (user.email or '').lower() != str(data.get('email', '')).lower():
        messages.error(request, 'Ссылка устарела: адрес почты был изменён. Запросите новое письмо.')
        return redirect(where)
    if email_taken(user.email, exclude_user=user):
        messages.error(request, 'Эта почта уже подтверждена другим аккаунтом.')
        return redirect(where)
    profile_obj, _ = UserProfile.objects.get_or_create(user=user)
    profile_obj.email_verified = True
    profile_obj.save(update_fields=['email_verified'])
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=['is_active'])
    messages.success(request, 'Почта подтверждена! Аккаунт активен.')
    return redirect(where)


def resend_verification(request):
    # Повторная отправка письма: вошедшему пользователю - на почту из профиля,
    # не подтвердившему регистрацию - на введённый адрес.
    if request.user.is_authenticated:
        if request.method == 'POST':
            if not request.user.email:
                messages.error(request, 'Сначала привяжите почту в профиле.')
            elif try_send_verification(request, request.user):
                messages.success(request, f'Письмо отправлено на {request.user.email}.')
            else:
                messages.error(request, 'Не удалось отправить письмо. Проверьте настройки почты (EMAIL_* в файле .env).')
        return redirect('profile')
    form = EmailForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        pending = get_user_model().objects.filter(email__iexact=form.cleaned_data['email'], is_active=False).first()
        if pending is not None:
            try_send_verification(request, pending)
        # Ответ одинаковый в любом случае, чтобы нельзя было выяснить, какие адреса есть в базе.
        messages.success(request, 'Если эта почта ждёт подтверждения, мы отправили письмо повторно.')
        return redirect('login')
    return render(request, TEMPLATE, {'page': 'resend', 'form': form})


# ======================================================================
# CRUD викторин (Django Forms) - только роль «Администратор/Создатель»
# ======================================================================
class QuizListView(LoginRequiredMixin, ListView):
    template_name = TEMPLATE
    context_object_name = 'quizzes'
    extra_context = {'page': 'quiz_list'}

    def get_queryset(self):
        return visible_quizzes(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        quizzes = list(context['quizzes'])
        if is_student(self.request.user):
            # Для студента отмечаем викторины, которые он уже прошёл.
            done = {a.quiz_id: a for a in QuizAttempt.objects.filter(
                user=self.request.user, is_valid=True, finished_at__isnull=False)}
            for quiz in quizzes:
                quiz.my_attempt = done.get(quiz.pk)
        # Сколько отзывов у каждой викторины (для значка 💬 на карточках).
        counts = dict(QuizAttempt.objects.filter(is_valid=True, finished_at__isnull=False)
                      .exclude(review="").order_by().values_list('quiz_id').annotate(n=Count('id')))
        for quiz in quizzes:
            quiz.review_total = counts.get(quiz.pk, 0)
        context['quizzes'] = quizzes
        context['leaders'] = get_leaderboard(limit=10)
        return context


class QuizDetailView(LoginRequiredMixin, DetailView):
    template_name = TEMPLATE
    context_object_name = 'quiz'
    extra_context = {'page': 'quiz_detail'}

    def get_queryset(self):
        return visible_quizzes(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if is_student(self.request.user):
            attempts = QuizAttempt.objects.filter(user=self.request.user, quiz=self.object)
            context['attempts'] = attempts
            context['completed'] = attempts.filter(is_valid=True, finished_at__isnull=False).first()
            if context['completed']:
                context['review_form'] = ReviewForm(initial={'review': context['completed'].review})
        context['liked'] = self.object.likes.filter(pk=self.request.user.pk).exists()
        context['reviews'] = quiz_reviews(self.object)
        return context


class QuizCreateView(CreatorRequiredMixin, CreateView):
    model = Quiz
    form_class = QuizForm
    template_name = TEMPLATE
    extra_context = {'page': 'quiz_form', 'title': 'Новая викторина'}

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Викторина создана. Теперь добавьте вопросы.')
        return super().form_valid(form)


class QuizUpdateView(CreatorRequiredMixin, UpdateView):
    model = Quiz
    form_class = QuizForm
    template_name = TEMPLATE
    extra_context = {'page': 'quiz_form', 'title': 'Редактирование викторины'}

    def form_valid(self, form):
        messages.success(self.request, 'Изменения сохранены.')
        return super().form_valid(form)


class QuizDeleteView(CreatorRequiredMixin, DeleteView):
    model = Quiz
    template_name = TEMPLATE
    context_object_name = 'quiz'
    success_url = reverse_lazy('quiz-list')
    extra_context = {'page': 'quiz_confirm_delete'}

    def form_valid(self, form):
        messages.success(self.request, 'Викторина удалена.')
        return super().form_valid(form)


class QuestionCreateView(CreatorRequiredMixin, CreateView):
    model = Question
    form_class = QuestionForm
    template_name = TEMPLATE
    extra_context = {'page': 'question_form'}

    def get_quiz(self):
        return get_object_or_404(Quiz, pk=self.kwargs['quiz_pk'])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['quiz'] = self.get_quiz()
        return context

    def form_valid(self, form):
        form.instance.quiz = self.get_quiz()
        messages.success(self.request, 'Вопрос добавлен.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('quiz-detail', args=[self.kwargs['quiz_pk']])


@login_required
@require_POST
def question_delete(request, pk):
    if not is_creator(request.user):
        raise PermissionDenied
    question = get_object_or_404(Question, pk=pk)
    quiz = question.quiz
    question.delete()
    messages.success(request, 'Вопрос удалён.')
    return redirect(quiz)


# ======================================================================
# Прохождение викторины: таймер, подсчёт баллов (роль «Студент»)
# ======================================================================
@login_required
@require_POST
def rating_set(request, user_id):
    # Администратор задаёт игроку итоговые баллы в общем рейтинге
    # (разница с реальной суммой баллов хранится как корректировка).
    if not is_creator(request.user):
        raise PermissionDenied
    target = get_object_or_404(get_user_model(), pk=user_id)
    try:
        desired = int(request.POST.get('score', ''))
    except ValueError:
        desired = -1
    if not 0 <= desired <= 100000:
        messages.error(request, 'Введите целое число баллов от 0 до 100000.')
        return redirect(_back(request, reverse('leaderboard')))
    profile_obj, _ = UserProfile.objects.get_or_create(user=target)
    profile_obj.rating_bonus = desired - base_score(target)
    profile_obj.save(update_fields=['rating_bonus'])
    messages.success(request, f'Баллы игрока {target.username} в рейтинге: {desired}.')
    return redirect(_back(request, reverse('leaderboard')))


@login_required
@require_POST
def rating_visibility(request, user_id, show):
    # Администратор убирает игрока из рейтинга или возвращает его обратно.
    if not is_creator(request.user):
        raise PermissionDenied
    target = get_object_or_404(get_user_model(), pk=user_id)
    profile_obj, _ = UserProfile.objects.get_or_create(user=target)
    profile_obj.in_rating = show
    profile_obj.save(update_fields=['in_rating'])
    if show:
        messages.success(request, f'Игрок {target.username} возвращён в рейтинг.')
    else:
        messages.success(request, f'Игрок {target.username} убран из рейтинга.')
    return redirect(_back(request, reverse('leaderboard')))


@login_required
@require_POST
def quiz_like(request, pk):
    # Лайк-переключатель: нажали - поставили, нажали ещё раз - сняли.
    quiz = get_object_or_404(visible_quizzes(request.user), pk=pk)
    if quiz.likes.filter(pk=request.user.pk).exists():
        quiz.likes.remove(request.user)
        liked = False
    else:
        quiz.likes.add(request.user)
        liked = True
    if request.headers.get('x-requested-with') == 'fetch':
        return JsonResponse({'liked': liked, 'count': quiz.likes.count()})
    return redirect(quiz)


@login_required
@require_POST
def quiz_review(request, pk):
    # Отзыв о викторине: только студент, который её прошёл (засчитанная попытка).
    if not is_student(request.user):
        raise PermissionDenied
    quiz = get_object_or_404(Quiz, pk=pk, is_published=True)
    attempt = QuizAttempt.objects.filter(
        user=request.user, quiz=quiz, is_valid=True, finished_at__isnull=False).first()
    if attempt is None:
        messages.error(request, 'Оставить отзыв можно только после прохождения викторины.')
        return redirect(quiz)
    form = ReviewForm(request.POST)
    if form.is_valid():
        attempt.review = form.cleaned_data['review']
        attempt.reviewed_at = timezone.now()
        attempt.save(update_fields=['review', 'reviewed_at'])
        messages.success(request, 'Спасибо! Ваш отзыв сохранён и виден другим игрокам.')
    else:
        problems = ' '.join(error for errors in form.errors.values() for error in errors)
        logger.warning('Отзыв не сохранён (user=%s, quiz=%s): %s', request.user.pk, quiz.pk, problems)
        messages.error(request, 'Отзыв не сохранён: ' + problems)
    return redirect(_back(request, quiz.get_absolute_url()))


@login_required
@require_POST
def review_delete(request, pk):
    # Удалить отзыв может его автор или администратор.
    attempt = get_object_or_404(QuizAttempt.objects.select_related('quiz'), pk=pk)
    if attempt.user_id != request.user.id and not is_creator(request.user):
        raise PermissionDenied
    attempt.review = ""
    attempt.reviewed_at = None
    attempt.save(update_fields=['review', 'reviewed_at'])
    messages.success(request, 'Отзыв удалён.')
    return redirect(_back(request, attempt.quiz.get_absolute_url()))


@login_required
@require_POST
def quiz_generate(request):
    # Создаёт случайную викторину из банка вопросов (программирование или логика).
    if not is_creator(request.user):
        raise PermissionDenied
    category = request.POST.get('category', '')
    if category not in QUESTION_BANK:
        messages.error(request, 'Неизвестная категория.')
        return redirect('quiz-list')
    quiz = create_random_quiz(category, request.user)
    messages.success(request, f'Создана викторина «{quiz.title}»: {quiz.question_count} вопросов.')
    return redirect(quiz)


@login_required
@require_POST
def quiz_start(request, pk):
    if not is_student(request.user):
        raise PermissionDenied
    quiz = get_object_or_404(Quiz, pk=pk, is_published=True)
    if not quiz.questions.exists():
        messages.error(request, 'В этой викторине пока нет вопросов.')
        return redirect(quiz)
    try:
        attempt = QuizAttempt.start_for(request.user, quiz)
    except AttemptNotAllowed as exc:
        messages.error(request, str(exc))
        done = QuizAttempt.objects.filter(user=request.user, quiz=quiz, is_valid=True,
                                          finished_at__isnull=False).first()
        return redirect('attempt-result', pk=done.pk) if done else redirect(quiz)
    return redirect('attempt-take', pk=attempt.pk)


@login_required
def attempt_take(request, pk):
    attempt = get_object_or_404(QuizAttempt.objects.select_related('quiz'), pk=pk, user=request.user)
    if attempt.is_finished:
        return redirect('attempt-result', pk=attempt.pk)
    quiz = attempt.quiz

    if request.method == 'POST':
        form = AttemptForm(quiz, request.POST)
        if form.is_valid():
            # Серверная проверка таймера выполняется внутри attempt.finish().
            attempt.finish(form.get_answers())
            if not attempt.is_valid:
                messages.error(request, 'Время вышло: попытка аннулирована, начислено 0 баллов.')
            return redirect('attempt-result', pk=attempt.pk)
    else:
        form = AttemptForm(quiz)

    context = {
        'page': 'attempt',
        'quiz': quiz,
        'attempt': attempt,
        'form': form,
        # Остаток времени считает сервер; JS лишь отображает обратный отсчёт.
        'remaining_seconds': attempt.remaining_seconds(),
    }
    return render(request, TEMPLATE, context)


@login_required
def attempt_result(request, pk):
    attempt = get_object_or_404(QuizAttempt.objects.select_related('quiz', 'user'), pk=pk, finished_at__isnull=False)
    if attempt.user_id != request.user.id and not is_creator(request.user):
        raise PermissionDenied
    context = {'page': 'result', 'attempt': attempt, 'review': attempt.wrong_answers(),
               'reviews': quiz_reviews(attempt.quiz)}
    if attempt.is_valid and attempt.user_id == request.user.id and is_student(request.user):
        # Форма отзыва прямо на странице результата.
        context['review_form'] = ReviewForm(initial={'review': attempt.review})
    return render(request, TEMPLATE, context)


@login_required
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
                new_email = form.cleaned_data.get('email', '')
                if new_email != (target.email or '').lower():
                    # Почту изменили или привязали: она считается неподтверждённой, отправляем письмо.
                    target.email = new_email
                    target.save(update_fields=['email'])
                    profile_obj.email_verified = False
                    profile_obj.save(update_fields=['email_verified'])
                    if not new_email:
                        messages.success(request, 'Профиль обновлён, почта отвязана.')
                    elif try_send_verification(request, target):
                        messages.success(request, f'Профиль обновлён. Письмо для подтверждения отправлено на {new_email}.')
                    else:
                        messages.error(request, 'Профиль обновлён, но письмо отправить не удалось. Нажмите «Отправить письмо снова».')
                else:
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
        'total_score': sum(a.score for a in results) + profile_obj.rating_bonus,
    }
    return render(request, TEMPLATE, context)


@login_required
def leaderboard(request):
    quizzes = Quiz.objects.filter(is_published=True)
    selected = None
    quiz_id = request.GET.get('quiz', '')
    if quiz_id.isdigit():
        selected = quizzes.filter(pk=quiz_id).first()
    context = {
        'page': 'leaderboard',
        'entries': get_leaderboard(selected),
        'quizzes': quizzes,
        'selected_quiz': selected,
        'hidden_players': (UserProfile.objects.filter(in_rating=False).select_related('user')
                           if is_creator(request.user) else []),
    }
    return render(request, TEMPLATE, context)


# ======================================================================
# REST API (Django REST Framework)
# ======================================================================
class IsStudentRole(permissions.BasePermission):
    message = 'Доступно только пользователям с ролью «Студент».'

    def has_permission(self, request, view):
        return is_student(request.user)


class QuizListAPIView(ListAPIView):
    # GET /api/quizzes/ - список викторин (студент видит только опубликованные).
    serializer_class = QuizSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return visible_quizzes(self.request.user)


class QuizStartAPIView(APIView):
    # POST /api/quizzes/<id>/start/ - старт попытки, запускает серверный таймер.
    permission_classes = [permissions.IsAuthenticated, IsStudentRole]

    def post(self, request, pk):
        quiz = get_object_or_404(Quiz, pk=pk, is_published=True)
        if not quiz.questions.exists():
            return Response({'detail': 'В викторине нет вопросов.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            attempt = QuizAttempt.start_for(request.user, quiz)
        except AttemptNotAllowed as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(AttemptStartSerializer(attempt).data, status=status.HTTP_201_CREATED)


class QuizSubmitAPIView(APIView):
    # POST /api/quizzes/<id>/submit/ - отправка ответов, проверка таймера и баллы.
    permission_classes = [permissions.IsAuthenticated, IsStudentRole]

    def post(self, request, pk):
        quiz = get_object_or_404(Quiz, pk=pk, is_published=True)
        attempt = (QuizAttempt.objects
                   .filter(user=request.user, quiz=quiz, finished_at__isnull=True)
                   .order_by('-started_at').first())
        if attempt is None:
            return Response({'detail': 'Нет активной попытки. Сначала вызовите /start/.'},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = AnswerSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attempt.finish(serializer.validated_data['answers'])
        return Response(AttemptResultSerializer(attempt).data)


class LeaderboardAPIView(APIView):
    # GET /api/leaderboard/?quiz=<id> - таблица лидеров.
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        quiz = None
        quiz_id = request.query_params.get('quiz')
        if quiz_id:
            if not quiz_id.isdigit():
                return Response({'detail': 'Параметр quiz должен быть числом.'}, status=status.HTTP_400_BAD_REQUEST)
            quiz = get_object_or_404(visible_quizzes(request.user), pk=quiz_id)
        return Response(LeaderboardEntrySerializer(get_leaderboard(quiz), many=True).data)


# ======================================================================
# Маршруты (ROOT_URLCONF = quiz_platform.views)
# ======================================================================
urlpatterns = [
    path('admin/', admin.site.urls),

    path('login/', LoginView.as_view(template_name=TEMPLATE, authentication_form=LoginForm,
                                  extra_context={'page': 'login'}), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('register/', register, name='register'),
    path('verify/resend/', resend_verification, name='email-resend'),
    path('verify/<str:token>/', verify_email, name='verify-email'),

    path('', QuizListView.as_view(), name='quiz-list'),
    path('quiz/create/', QuizCreateView.as_view(), name='quiz-create'),
    path('quiz/<int:pk>/', QuizDetailView.as_view(), name='quiz-detail'),
    path('quiz/<int:pk>/edit/', QuizUpdateView.as_view(), name='quiz-update'),
    path('quiz/<int:pk>/delete/', QuizDeleteView.as_view(), name='quiz-delete'),
    path('quiz/<int:quiz_pk>/questions/add/', QuestionCreateView.as_view(), name='question-add'),
    path('questions/<int:pk>/delete/', question_delete, name='question-delete'),
    path('quiz/generate/', quiz_generate, name='quiz-generate'),

    path('quiz/<int:pk>/start/', quiz_start, name='quiz-start'),
    path('quiz/<int:pk>/like/', quiz_like, name='quiz-like'),
    path('quiz/<int:pk>/review/', quiz_review, name='quiz-review'),
    path('review/<int:pk>/delete/', review_delete, name='review-delete'),
    path('attempt/<int:pk>/', attempt_take, name='attempt-take'),
    path('attempt/<int:pk>/result/', attempt_result, name='attempt-result'),
    path('leaderboard/', leaderboard, name='leaderboard'),
    path('rating/<int:user_id>/set/', rating_set, name='rating-set'),
    path('rating/<int:user_id>/hide/', rating_visibility, {'show': False}, name='rating-hide'),
    path('rating/<int:user_id>/show/', rating_visibility, {'show': True}, name='rating-show'),
    path('profile/', profile, name='profile'),
    path('profile/<str:username>/', profile, name='profile-user'),

    path('api/quizzes/', QuizListAPIView.as_view(), name='api-quiz-list'),
    path('api/quizzes/<int:pk>/start/', QuizStartAPIView.as_view(), name='api-quiz-start'),
    path('api/quizzes/<int:pk>/submit/', QuizSubmitAPIView.as_view(), name='api-quiz-submit'),
    path('api/leaderboard/', LeaderboardAPIView.as_view(), name='api-leaderboard'),
]

# Аватарки лежат в MEDIA_ROOT; в режиме DEBUG их раздаёт сам Django.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
