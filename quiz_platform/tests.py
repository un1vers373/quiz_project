from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import (ROLE_ADMIN, ROLE_STUDENT, AttemptNotAllowed, Question, Quiz, QuizAttempt, UserProfile,
                     get_leaderboard)

PASSWORD = 'Str0ng-pass-123'


def make_user(username, role):
    user = User.objects.create_user(username, password=PASSWORD)
    UserProfile.objects.filter(user=user).update(role=role)
    return user


class QuizPlatformTests(TestCase):
    def setUp(self):
        self.admin = make_user('teacher', ROLE_ADMIN)
        self.alice = make_user('alice', ROLE_STUDENT)
        self.bob = make_user('bob', ROLE_STUDENT)
        self.quiz = Quiz.objects.create(
            title='Python basics', description='Тест', time_limit_minutes=10,
            is_published=True, created_by=self.admin,
        )
        self.q1 = Question.objects.create(
            quiz=self.quiz, text='2 + 2 = ?', option_a='3', option_b='4',
            option_c='5', option_d='6', correct_option='B', points=2,
        )
        self.q2 = Question.objects.create(
            quiz=self.quiz, text='Столица Кыргызстана?', option_a='Бишкек', option_b='Ош',
            option_c='Каракол', option_d='Талас', correct_option='A', points=3,
        )

    # 1. Создание викторины администратором
    def test_admin_can_create_quiz(self):
        self.client.login(username='teacher', password=PASSWORD)
        response = self.client.post(reverse('quiz-create'), {
            'title': 'Django ORM', 'description': 'Тест по ORM',
            'time_limit_minutes': 15, 'is_published': 'on',
        })
        quiz = Quiz.objects.get(title='Django ORM')
        self.assertRedirects(response, reverse('quiz-detail', args=[quiz.pk]))
        self.assertEqual(quiz.created_by, self.admin)
        self.assertTrue(quiz.is_published)

    # 2. Ограничение прав студента
    def test_student_cannot_manage_quizzes(self):
        self.client.login(username='alice', password=PASSWORD)
        data = {'title': 'Hack', 'description': 'x', 'time_limit_minutes': 5}
        self.assertEqual(self.client.post(reverse('quiz-create'), data).status_code, 403)
        self.assertFalse(Quiz.objects.filter(title='Hack').exists())
        self.assertEqual(self.client.post(reverse('quiz-delete', args=[self.quiz.pk])).status_code, 403)
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())
        self.assertEqual(self.client.post(reverse('question-add', args=[self.quiz.pk]), {}).status_code, 403)

    # 3. Логика проверки таймера и правило одной попытки
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

    # 4. Начисление баллов и попадание в рейтинг
    def test_scores_reach_leaderboard(self):
        plan = (('alice', 'B', 'A'), ('bob', 'B', 'C'))
        for username, answer1, answer2 in plan:
            self.client.login(username=username, password=PASSWORD)
            self.client.post(reverse('quiz-start', args=[self.quiz.pk]))
            attempt = QuizAttempt.objects.get(user__username=username, quiz=self.quiz)
            response = self.client.post(reverse('attempt-take', args=[attempt.pk]), {
                f'q_{self.q1.pk}': answer1,
                f'q_{self.q2.pk}': answer2,
            })
            self.assertRedirects(response, reverse('attempt-result', args=[attempt.pk]))
            self.client.logout()

        board = get_leaderboard(self.quiz)
        self.assertEqual([row['username'] for row in board], ['alice', 'bob'])
        self.assertEqual([row['total_score'] for row in board], [5, 2])
        self.assertEqual(board[0]['rank'], 1)

        self.client.login(username='alice', password=PASSWORD)
        page = self.client.get(reverse('leaderboard'))
        self.assertContains(page, 'alice')
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

        # Лайк (переключатель) и отзыв после прохождения
        self.client.post(reverse('quiz-like', args=[self.quiz.pk]))
        self.assertEqual(self.quiz.likes.count(), 1)
        self.client.post(reverse('quiz-review', args=[self.quiz.pk]), {'review': 'Отличная викторина!'})
        bob_attempt.refresh_from_db()
        self.assertEqual(bob_attempt.review, 'Отличная викторина!')
        detail = self.client.get(reverse('quiz-detail', args=[self.quiz.pk]))
        self.assertContains(detail, 'Отличная викторина!')
        self.client.post(reverse('quiz-like', args=[self.quiz.pk]))
        self.assertEqual(self.quiz.likes.count(), 0)

        # Администратор меняет баллы игрока и убирает игрока из рейтинга; студенту это запрещено
        self.assertEqual(self.client.post(reverse('rating-hide', args=[self.alice.pk])).status_code, 403)
        self.client.logout()
        self.client.login(username='teacher', password=PASSWORD)
        self.client.post(reverse('rating-set', args=[self.alice.pk]), {'score': '100'})
        self.assertEqual(get_leaderboard()[0]['total_score'], 100)
        self.client.post(reverse('rating-hide', args=[self.bob.pk]))
        self.assertEqual([row['username'] for row in get_leaderboard()], ['alice'])

    # 5. Работа REST API и проверка прав
    def test_api_endpoints_and_permissions(self):
        api = APIClient()
        self.assertIn(api.get(reverse('api-quiz-list')).status_code, (401, 403))

        api.login(username='alice', password=PASSWORD)
        listing = api.get(reverse('api-quiz-list'))
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]['question_count'], 2)
        self.assertEqual(listing.json()[0]['total_points'], 5)

        start = api.post(reverse('api-quiz-start', args=[self.quiz.pk]))
        self.assertEqual(start.status_code, 201)
        self.assertNotIn('correct_option', start.json()['questions'][0])

        submit = api.post(
            reverse('api-quiz-submit', args=[self.quiz.pk]),
            {'answers': {str(self.q1.pk): 'B', str(self.q2.pk): 'A'}},
            format='json',
        )
        self.assertEqual(submit.status_code, 200)
        self.assertEqual(submit.json()['score'], 5)
        self.assertTrue(submit.json()['is_valid'])

        board = api.get(reverse('api-leaderboard'))
        self.assertEqual(board.status_code, 200)
        self.assertEqual(board.json()[0]['username'], 'alice')

        # Создатель не может проходить викторины через API
        api.logout()
        api.login(username='teacher', password=PASSWORD)
        self.assertEqual(api.post(reverse('api-quiz-start', args=[self.quiz.pk])).status_code, 403)
