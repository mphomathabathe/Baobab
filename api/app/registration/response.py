from datetime import date
import traceback
import json

from flask_restful import reqparse, fields, marshal_with
import flask_restful as restful
from flask import g, request

from sqlalchemy.exc import SQLAlchemyError

from app.registration.mixins import RegistrationResponseMixin
from app.responses.models import Response, Answer
from app.users.models import AppUser
from app.registration.models import Offer, Registration, RegistrationAnswer, RegistrationForm, RegistrationQuestion, RegistrationSection
from app.utils.auth import auth_required
from app.utils import errors, emailer, strings
from app import LOGGER
from app.users.repository import UserRepository as user_repository


from app import db, bcrypt

def _get_answer_value(answer, question):
    if question.type == 'multi-choice' and question.options is not None:
        value = [o for o in question.options if o['value'] == answer.value]
        if not value:
            return answer.value
        return value[0]['label']

    if question.type == 'file' and answer.value:
        return 'Uploaded File'

    return answer.value


class RegistrationApi(RegistrationResponseMixin, restful.Resource):
    answer_fields = {
        'id' : fields.Integer,
        'registration_id': fields.Integer,
        'registration_question_id': fields.Integer,
        'value': fields.String
    }

    registration_fields = {
        'id': fields.Integer,
        'offer_id': fields.Integer,
        'registration_form_id': fields.Integer,
        'confirmed': fields.Boolean,
        'created_at': fields.DateTime,
        'confirmation_email_sent_at': fields.DateTime

    }

    response_fields = {
        'registration_id': fields.Integer,
        'offer_id': fields.Integer,
        'registration_form_id': fields.Integer,
        'answers': fields.List(fields.Nested(answer_fields))
    }

    @auth_required
    def get(self):

        try:
            user_id = g.current_user['id']

            dbOffer = db.session.query(Offer).filter(Offer.user_id == user_id).first()

            if dbOffer is None:
                return 'No offer', 404
            registration = db.session.query(Registration).filter(Registration.offer_id == dbOffer.id).first()
            if registration is None:
                return 'no Registration', 404

            registrationForm = db.session.query(RegistrationForm).filter(RegistrationForm.id == registration.registration_form_id).first()

            if registrationForm is None :
                return 'no registration form found', 404
            dbAnswers = db.session.query(RegistrationAnswer).filter(RegistrationAnswer.registration_id == registration.id).all()

            response = {
                'registration_id':registration.id,
                'offer_id': dbOffer.id,
                'registration_form_id': registrationForm.id,
                'answers':dbAnswers
            }

            return json.dumps(response, indent=4)


        except Exception as e:
            return e
            LOGGER.error("Database error encountered: {}".format(e))
            return errors.DB_NOT_AVAILABLE
        except:
            LOGGER.error("Encountered unknown error: {}".format(traceback.format_exc()))
            return errors.DB_NOT_AVAILABLE

    @auth_required
    @marshal_with(registration_fields)
    def post(self):
        # Save a new response for the logged-in user.
        req_parser = reqparse.RequestParser()
        args = self.req_parser.parse_args()
        offer_id = args['offer_id']

        try:
            dbOffer = db.session.query(Offer).filter(Offer.id == offer_id).first()

            if dbOffer is None:
                return 'offer not found', 404

            current_user = user_repository.get_by_id(dbOffer.user_id)
            if current_user is None:
                return 'user not found', 404

            registrationForm = db.session.query(RegistrationForm).filter(
                RegistrationForm.id == args['registration_form_id'])

            if not registrationForm.first():
                dbRegistrationForm = RegistrationForm(
                    id=args['registration_form_id'],
                    event_id=1
                )
                db.session.add(dbRegistrationForm)
                db.session.commit()


            registration = Registration(
                offer_id=args['offer_id'],
                registration_form_id=args['registration_form_id'],
                confirmed=False,
                confirmation_email_sent_at=date.today()
            )
            db.session.add(registration)
            db.session.commit()

            for answer_args in args['answers']:
                if db.session.query(RegistrationQuestion).filter(
                        RegistrationQuestion.id == answer_args['registration_question_id']).first():

                    answer = RegistrationAnswer(registration_id=registration.id,
                                                registration_question_id=answer_args['registration_question_id'],
                                                value=answer_args['value'])

                    db.session.add(answer)
            db.session.commit()

            registrationAnswers = db.session.query(RegistrationAnswer).filter(
                RegistrationAnswer.registration_id == registration.id).all()
            registrationQuestions = db.session.query(RegistrationQuestion).filter(
                RegistrationQuestion.registration_form_id == args['registration_form_id']).all()

            self.send_confirmation(current_user, registrationQuestions, registrationAnswers, registration.confirmed)

        except SQLAlchemyError as e:
            return e
            LOGGER.error("Database error encountered: {}".format(e))
            return errors.DB_NOT_AVAILABLE
        except Exception as e:
            return e
            LOGGER.error("Encountered unknown error: {}".format(traceback.format_exc()))
            return errors.DB_NOT_AVAILABLE
        finally:
            return registration, 201  # 201 is 'CREATED' status code

    # @auth_required
    # @marshal_with(response_fields)
    def put(self):
        # Update an existing response for the logged-in user.
        req_parser = reqparse.RequestParser()
        args = self.req_parser.parse_args()
        # user_id = g.current_user['id']

        registration = db.session.query(Registration).filter(Registration.id == args['registration_id']).first()
        if registration is None:
            return 'Registration not found', 404

        dbOffer = db.session.query(Offer).filter(Offer.id == registration.offer_id).first()

        if dbOffer is None:
            return "Offer not found", 404

        # if dbOffer.user_id != user_id:
        #     return "Forbidden", 401

        try:

            registration.registration_form_id = args['registration_form_id']
            db.session.commit()

            for answer_args in args['answers']:
                answer = db.session.query(RegistrationAnswer).filter(RegistrationAnswer.registration_question_id
                                                                     == answer_args['registration_question_id']).first()
                if answer is not None:
                    answer.value = answer_args['value']

                elif db.session.query(RegistrationQuestion).filter(
                        RegistrationQuestion.id == answer_args['registration_question_id']).first():

                    answer = RegistrationAnswer(registration_id=registration.id,
                                                registration_question_id=answer_args['registration_question_id'],
                                                value=answer_args['value'])

                    db.session.add(answer)
            db.session.commit()
        except:
            return 'Could not access DB', 400
        finally:
            return 200


    def send_confirmation(self, user, questions, answers, confirmed):
        if answers is None:
            LOGGER.warn(
                'Found no answers associated with response with id {response_id}'.format(response_id=user.id))
        if questions is None:
            LOGGER.warn(
                'Found no questions associated with application form with id {form_id}'.format(form_id=user.id))
        try:
            # Building the summary, where the summary is a dictionary whose key is the question headline, and the value is the relevant answer
            summary = ""
            for answer in answers:
                for question in questions:
                    if answer.registration_question_id == question.id:
                        summary += "Question heading :" + question.headline + "\nQuestion Description :" + \
                           question.description + "\nAnswer :" + _get_answer_value(
                                answer, question) + "\n"

            subject = 'Registration'
            greeting = strings.build_response_email_greeting(user.user_title, user.firstname, user.lastname)
            if len(summary) <= 0:
                summary = '\nNo valid questions were answered'
            body_text = greeting + self.get_confirmed_message(confirmed) + '\n\n' + summary

            emailer.send_mail(user.email, subject, body_text=body_text)

        except Exception as e:
            LOGGER.error('Could not send confirmation email for response with id : {response_id}'.format(
                response_id=user.id))

    def get_confirmed_message(self, confirmed):
        if not confirmed:
            return '\nregistration is pending confirmation on receipt of payment.\n\n'
        else:
            return ''