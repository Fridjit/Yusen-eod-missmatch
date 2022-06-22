from flask import Flask, request
import telebot
from telebot import types
from flask_sqlalchemy import SQLAlchemy
import requests
import csv
import re

import config

# ======================================================================================================================
# config initialisation
telebot_secret = config.telebot_secret
token = config.telebot_token
url = config.url + telebot_secret
admin_bot_list = config.admin_bot_list

# Unique Identifiers initialisation from config
scac = config.scac

# ======================================================================================================================
# MySQL initialisation
mysql_host = 'localhost'
mysql_user = config.mysql_user
mysql_password = config.mysql_password
mysql_database = config.mysql_database

# ======================================================================================================================
# bot initialisation
bot = telebot.TeleBot(token, threaded=False)
bot.remove_webhook()
bot.set_webhook(url=url, drop_pending_updates=True)
for bot_admin in admin_bot_list:
    bot.send_message(bot_admin, 'Bot has been restarted', disable_notification=True, reply_markup=None)

# ======================================================================================================================
# Flask app as a web application, have ho idea how this works in detail, but it handles all to bot connections
# including: Telegram API, Site connections, Personal POST requests through Postman
app = Flask(__name__)

# SQLAlchemy initialisation
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql://{mysql_user}:{mysql_password}@{mysql_host}:5432/{mysql_database}'

db = SQLAlchemy(app)


# database model to authenticate users
class Users(db.Model):
    __tablename__ = 'bmkj'

    # Telegram ID as a primary key since it's unique to each user
    id = db.Column(db.BigInteger, primary_key=True, unique=True)
    name = db.Column(db.String(64))
    position_in_menu = db.Column(db.Integer, default='-2')
    current_customer = db.Column(db.String(20), default='Target')
    current_shift = db.Column(db.String(2), default='AM')

    def __init__(self, **kwargs):
        for property, value in kwargs.items():
            # depending on whether value is an iterable or not, we must
            # unpack it's value (when **kwargs is request.form, some values
            # will be a 1-element list)
            if hasattr(value, '__iter__') and not isinstance(value, str):
                # the ,= unpack of a singleton fails PEP8 (travis flake8 test)
                value = value[0]

            setattr(self, property, value)

    def __repr__(self):
        return str(self.id)


db.create_all()


# ======================================================================================================================
# Telegram API Webhook
@app.route('/' + telebot_secret, methods=['POST'])
def telegram_webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
    bot.process_new_updates([update])
    return 'ok', 200


# ======================================================================================================================
# Utility functions

# Build the menu button
def build_menu(position_in_menu, user=None, is_admin=False):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    message = None

    if position_in_menu == 0:
        button = types.KeyboardButton('SORT')
        markup.row(button)
        button = types.KeyboardButton('EOD')
        markup.row(button)
        button = types.KeyboardButton('BOBTAILS')
        markup.row(button)
        message = 'Main menu'
    elif position_in_menu == 1:
        button = types.KeyboardButton('Current mode: "EOD"')
        markup.row(button)
        button = types.KeyboardButton('Change to search')
        markup.row(button)
        button = types.KeyboardButton('Back to main menu')
        markup.row(button)
        message = 'Click the "Mode" button for more info'
    elif position_in_menu == 2:
        button = types.KeyboardButton('Current mode: "SEARCH"')
        markup.row(button)
        button = types.KeyboardButton('Change to EOD')
        markup.row(button)
        button = types.KeyboardButton('Back to main menu')
        markup.row(button)
        message = 'Click the "Mode" button for more info'

    elif position_in_menu == 4:
        button = types.KeyboardButton('Customer: ' + user.current_customer)
        markup.row(button)
        button = types.KeyboardButton('Shift: ' + user.current_shift)
        markup.row(button)
        button = types.KeyboardButton('Back to main menu')
        markup.row(button)
        message = 'Bobtail submission mode\n\nClick "/help" for more info'

    elif position_in_menu == 5:
        button = types.KeyboardButton('Back to main menu')
        markup.row(button)
        message = 'Welcome to the sort menu, paste current work to sort'

    return message, markup


# Is the user a bot admin
def is_bot_admin(telegram_id):
    return True if telegram_id in admin_bot_list else False


# Search function
def search_for_an_ID_or_row(text, return_dictionary=False):
    matched = []
    file_path = 'temp/completed_moves_verified.csv'

    with open(file_path, 'r') as eod_log_file:
        reader = csv.reader(eod_log_file)
        reader.__next__()

        for i in reader:
            if re.search(f'{text + scac}', i[3]) and len(text) == 4:
                matched.append(i)
            elif i[4] and re.search(f'{text}', i[4]):
                matched.append(i)
            elif i[3] == text:
                matched.append(i)

    if not matched:
        return False

    if return_dictionary:
        return matched

    return_messages = []
    return_message = 'Matched Rows:'
    for i in matched:
        reply = '\n'
        for j in i:
            reply += j + ' '
        if len(reply) > 4096:
            reply = "row to long? report this to admin"
        if len(return_message) + len(reply) > 4096:
            return_messages.append(return_message)
            return_message = ''
        return_message += reply[:-1]
    return_messages.append(return_message)

    return return_messages


# EOD logic check function
# gets a message from a user and returns a reply based on input
def EOD_logic_check(message):
    try:
        eod_log = {}
        file_path = 'temp/completed_moves_verified.csv'

        with open(file_path, 'r') as eod_log_file:
            reader = csv.reader(eod_log_file)
            reader.__next__()
            for i in reader:
                eod_log.update({i[3]: i})

        dispatch_list = message.split('\n')
        for i in range(len(dispatch_list)):
            current_row = dispatch_list[i - 1].split(' ')
            dispatch_list[i - 1] = []
            for j in current_row:
                if j:
                    dispatch_list[i - 1].append(j)

        duplicate_list = {}

        for i in dispatch_list:
            if duplicate_list.get(i[0], 'N/F') == 'N/F':
                duplicate_list.update({i[0]: False})
            else:
                duplicate_list.update({i[0]: True})

        issued_moves = []
        broken_rows = []

        for i in dispatch_list:

            # check on length [Move_ID, container number, Move Type]
            if len(i) != 3:
                reply = ''
                for j in i:
                    reply += j + ' '
                reply = reply[:-1] + ' - row does not match format'
                broken_rows.append(reply)

            # empty move_id
            elif not i[0]:
                reply = ''
                for j in i:
                    reply += j + ' '
                reply = reply[:-1] + ' - row does not match format'
                broken_rows.append(reply)

            # if move_id is 4 digits only:
            elif len(i[0]) == 4 and not re.match(r'^0-9', i[0]):
                search_res = search_for_an_ID_or_row(i[0], return_dictionary=True)
                reply = i[0] + ' - Matched ID\'s:  '
                if search_res:
                    for j in search_res:
                        reply += ' ' + j[3] + ','
                    reply = reply[:-1]
                else:
                    reply = i[0] + ' - no match'
                issued_moves.append(reply)

            # correct scac check
            elif not i[0][-4:] == scac:
                reply = i[0] + ' - Wrong scac'
                if duplicate_list.get(i[0]):
                    reply += ', Duplicate ID'
                issued_moves.append(reply)

            # if container not found
            elif eod_log.get(i[0], 'N/F') == 'N/F':
                reply = i[0] + ' - Not Found'
                if duplicate_list.get(i[0]):
                    reply += ', Duplicate ID'
                issued_moves.append(reply)

            # all elif's bellow are found

            # if a bobtail(extra check if duplicate):
            elif eod_log.get(i[0])[6] == 'Bobtail':
                if duplicate_list.get(i[0]):
                    reply = i[0] + ' - duplicate Move ID'
                    issued_moves.append(reply)

            # container does not match
            elif not eod_log.get(i[0])[4] == i[1]:
                reply = i[0] + ' - container does not match'
                if duplicate_list.get(i[0]):
                    reply += ', duplicate'
                reply += '\n  Us: ' + i[1] + '\n  Yusen: ' + eod_log.get(i[0])[4]
                issued_moves.append(reply)

            # container match, but duplicate
            elif eod_log.get(i[0])[4] == i[1] and duplicate_list.get(i[0]):
                reply = i[0] + ' - container match, duplicate move ID'
                issued_moves.append(reply)

        # Build reply:
        if not (broken_rows or issued_moves):
            return ['Everything is correct!!!']

        return_messages = []
        return_message = 'Broken rows:'
        if broken_rows:
            for i in broken_rows:
                reply = '\n' + i
                if len(reply) > 4096:
                    print('Error: ' + reply)
                    reply = '\nrow to long? report this to admin'
                if len(return_message) + len(reply) > 4096:
                    return_messages.append(return_message)
                    return_message = ''
                return_message += reply
            return_messages.append(return_message)

        return_message = 'Moves with Issues:'
        if issued_moves:
            for i in issued_moves:
                reply = '\n' + i
                if len(reply) > 4096:
                    print('Error: ' + reply)
                    reply = '\nrow to long? report this to admin'
                if len(return_message) + len(reply) > 4096:
                    return_messages.append(return_message)
                    return_message = ''
                return_message += reply
            return_messages.append(return_message)
        else:
            return_messages.append('Everything else is correct!!!')

        if return_messages:
            return return_messages

    except Exception as e:
        print('An error has occurred: ' + str(e))
        return ['An error occurred, please report this to the manager with the message you sent to the bot']


# Sort logic in 1 spot
def split_sort_current_work(message):
    try:
        text = message.split('\n')
        locations = {'Taylor Way', 'Sumner 1', 'Sumner 2'}
        LOADED_TO_S1_S2 = []
        EMPTY_TO_TW = []
        UNDEFINED = []
        origin = ''
        destination = ''
        current_move_id = ''

        while text:
            i = text[0]

            if not i:
                pass

            elif (len(i) > 4 and re.search(rf'[0-9]{scac}', i)) or len(text) == 1:

                if not current_move_id:
                    current_move_id = i

                else:

                    # remove undefined
                    if not (origin and destination):
                        UNDEFINED.append(current_move_id)

                    # TW to S1/S2
                    elif origin == 'Taylor Way' and (destination == 'Sumner 1' or destination == 'Sumner 2'):
                        row = 'L ' + current_move_id + (' S2' if destination == 'Sumner 2' else ' S1')
                        LOADED_TO_S1_S2.append(row)

                    # S1/S2 to TW
                    elif destination == 'Taylor Way' and (origin == 'Sumner 1' or origin == 'Sumner 2'):
                        row = 'E ' + current_move_id + (' S2' if origin == 'Sumner 2' else '')
                        EMPTY_TO_TW.append(row)

                    current_move_id = i
                    origin = ''
                    destination = ''

            elif i in locations:
                if not origin:
                    origin = i

                else:
                    destination = i

            text.pop(0)

        return_messages = []
        if LOADED_TO_S1_S2:
            return_message = 'Loaded: '
            for i in LOADED_TO_S1_S2:
                reply = '\n`' + i + '`'
                if len(reply) > 4096:
                    reply = 'row to long? report this to admin'
                if len(return_message) + len(reply) > 4096:
                    return_messages.append(return_message)
                    return_message = ''
                return_message += reply
            return_messages.append(return_message)

        if EMPTY_TO_TW:
            return_message = 'Empty: '
            for i in EMPTY_TO_TW:
                reply = '\n`' + i + '`'
                if len(reply) > 4096:
                    reply = 'row to long? report this to admin'
                if len(return_message) + len(reply) > 4096:
                    return_messages.append(return_message)
                    return_message = ''
                return_message += reply
            return_messages.append(return_message)

        if UNDEFINED:
            return_message = 'Undefined: '
            for i in UNDEFINED:
                reply = '\n`' + i + '`'
                if len(reply) > 4096:
                    reply = 'row to long? report this to admin'
                if len(return_message) + len(reply) > 4096:
                    return_messages.append(return_message)
                    return_message = ''
                return_message += reply
            return_messages.append(return_message)

        return return_messages

    except Exception as e:
        print('Message giving the error below: ' + message)
        print('Error while sorting: ' + e)
        return ['Error while sorting, contact support for help']


# Request to SmartSheets to obtain FormToken
def get_form_token():
    url = "https://app.smartsheet.com/b/form/f6aacf211b2a4f10ae3bda2cfc6bce2a"
    r = requests.request("GET", url)

    if r.status_code != 200:
        print('Status ')
        return False

    needed_string = str(r.text.split('\n')[24])[28:48]

    if len(needed_string) != 20:
        print('FormToken length error')
        print(needed_string)
        return False

    return needed_string


# Request to SmartSheets to submit a bobtail
def submit_bobtail(customer, shift, origin, destination, driver_name, comment):
    form_token = get_form_token()

    if not form_token:
        print('No FormToken error')
        return False

    try:

        url = "https://forms.smartsheet.com/api/submit/f6aacf211b2a4f10ae3bda2cfc6bce2a"

        payload = {
            'data': '{"kqkzAPq":{"type":"STRING","value":"'+scac+'"},"zXlGWn2":{"type":"STRING","value":"Bobtail"},"EkrG8Ql":{"type":"STRING","value":"'+customer+'"},"Jn6Zrgm":{"type":"STRING","value":"'+shift+'"},"7AgLY0G":{"type":"STRING","value":"'+origin+'"},"11eEO6J":{"type":"STRING","value":"'+destination+'"},"0kNKDaw":{"type":"STRING","value":"'+driver_name+'"},"7k6aRle":{"type":"STRING","value":"Completed"},"Yqd3MgE":{"type":"STRING","value":"'+comment+'"},"EMAIL_RECEIPT":{"type":"STRING","value":""}}'
        }

        headers = {
            'x-smar-forms-version': '1.122.0',
            'x-smar-submission-token': form_token
        }

        response = requests.request("POST", url, headers=headers, files=payload, timeout=1.3)

        return response.status_code

    except Exception as e:
        print(e)
        return e


def check_row(row_as_a_string, user):
    origins = {
        'AL': 'Alexander',
        'IGD': 'IGD',
        'TW': 'Taylor Way',
        'S1': 'Sumner 1',
        'S2': 'Sumner 2',
        'DP': 'DuPont'
    }
    destinations = {
        'AL': 'Alexander',
        'IGD': 'IGD',
        'TW': 'Taylor Way',
        'S1': 'Sumner 1',
        'S2': 'Sumner 2',
        'DP': 'DuPont'
    }

    try:
        raw_row = row_as_a_string.split(' ')
        row = []

        for i in raw_row:
            if i:
                row.append(i)

        if len(row) < 4:
            return False, None, None, None, None, None, None

        customer = user.current_customer
        if row[0] == 'Target' or row[0] == 'TJX' or row[0] == 'Yazaki':
            shift = row[0]
            row.pop(0)

        shift = user.current_shift
        if row[0] == 'AM' or row[0] == 'PM':
            shift = row[0]
            row.pop(0)

        if origins.get(row[0], 'N/F') == 'N/F':
            return False, None, None, None, None, None, None
        origin = origins.get(row[0])
        row.pop(0)

        if destinations.get(row[0], 'N/F') == 'N/F':
            return False, None, None, None, None, None, None
        destination = origins.get(row[0])
        row.pop(0)

        driver_name = row[0]
        row.pop(0)

        comment = ''
        for i in row:
            comment += i + ' '
        comment = comment[:-1]
        return True, customer, shift, origin, destination, driver_name, comment

    except Exception:
        return False, None, None, None, None, None, None


def bobtail_filter_and_submit(text, user):
    rows = text.split('\n')
    result = []

    for row in rows:
        status, customer, shift, origin, destination, driver_name, comment = check_row(row, user)
        if not status:
            result.append(row)
        elif not submit_bobtail(customer, shift, origin, destination, driver_name, comment) == 200:
            result.append(row)

    return_messages = []
    return_message = 'Errors: '
    for i in result:
        reply = '\n' + i
        if len(reply) > 4096:
            reply = 'row to long? report this to admin'
        if len(return_message) + len(reply) > 4096:
            return_messages.append(return_message)
            return_message = ''
        return_message += reply
    return_messages.append(return_message)

    if return_messages == ['Errors: ']:
        return ['Everything is submitted successfully']

    return return_messages


# ======================================================================================================================


# Bot listener

# /start command
@bot.message_handler(commands=['start'])
def start_command(m):
    user = Users.query.filter_by(id=m.from_user.id).first()

    if not user:
        user = Users(id=m.from_user.id, name=m.from_user.first_name)
        bot.send_message(admin_bot_list[0],
                         'New User:\n' + m.from_user.first_name + '\n'
                         '`' + str(m.from_user.id) + '`',
                         parse_mode='MarkdownV2')
        db.session.add(user)
        db.session.commit()
        bot.send_message(m.from_user.id,
                         'You are not registered, please request access from your manager.\n\n'
                         'Your id: `' + str(m.from_user.id) + '`(clickable), Give it to your manager',
                         parse_mode='MarkdownV2')
        return

    if user.position_in_menu == -1:
        return

    if user.position_in_menu == -2:
        user.position_in_menu = -1

        reply = 'You are not registered, please request access from your manager\n\n' \
                'Your id: `' + str(m.from_user.id) + '`(clickable), Give it to your manager\n\n' \
                'This is the last time you get this message. The bot is for private ' \
                'use only, so until you are granted access you will be ignored. Thank you.'
        reply_markup = None

        if is_bot_admin(m.from_user.id):
            user.position_in_menu = 0
            reply, reply_markup = build_menu(0)
            reply = 'You have been registered, Master'

        db.session.commit()
        bot.send_message(m.from_user.id, reply, reply_markup=reply_markup, parse_mode='MarkdownV2')
        return

    message, reply_markup = build_menu(0)
    user.position_in_menu = 0
    db.session.commit()

    bot.send_message(m.from_user.id, 'Menu has been reset', reply_markup=reply_markup)


# /test command
@bot.message_handler(commands=['test'])
def test_command(m):
    if not is_bot_admin(m.from_user.id):
        return

    '''
    db.drop_all()
    db.create_all()

    user = Users(id=m.from_user.id, name=m.from_user.first_name, position_in_menu=0)
    db.session.add(user)
    db.session.commit()
    print('Databases recreated')
    '''

    # reply = str(submit_bobtail())

    reply = 'no test in progress'
    bot.send_message(m.from_user.id, reply)


# /help
@bot.message_handler(commands=['help'])
def help_command(m):
    user = Users.query.filter_by(id=m.from_user.id).first()

    if not user:
        user = Users(id=m.from_user.id, name=m.from_user.first_name, position_in_menu=-1)
        bot.send_message(admin_bot_list[0],
                         'New User:\n' + m.from_user.first_name + '\n'
                         '`' + str(m.from_user.id) + '`',
                         parse_mode='MarkdownV2')
        db.session.add(user)
        db.session.commit()
        message = 'You are not registered, please request access from your manager\n\n' \
                  'Your id: `' + str(m.from_user.id) + '`(clickable), Give it to your manager\n\n' \
                  'This is the last time you get this message. The bot is for private ' \
                  'use only, so until you are granted access you will be ignored. Thank you.'
        bot.send_message(m.from_user.id, message, parse_mode='MarkdownV2')
        return

    if user.position_in_menu < 0:
        return

    if user.position_in_menu == 0:
        message, reply_markup = build_menu(0)
        message = 'This is the main menu, use buttons below to navigate'
        if is_bot_admin(m.from_user.id):
            message = 'Admin features:\n' \
              'add [users_telegram_id]\n' \
              'remove [users_telegram_id]\n' \
              'list - list of user id\'s\n'

        bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
        return

    if user.position_in_menu == 1:
        message, reply_markup = build_menu(1)
        message = 'Current mode is EOD, the system tries to match yur report moves with the completed moves log you upload in advance\n' \
                  'For more info click the current mode button\n\n' \
                  'To change mode click the "Change to search"'
        bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
        return

    if user.position_in_menu == 2:
        message, reply_markup = build_menu(2)
        message = 'Current mode is search. click the current mode button on how to use this mode\n\n' \
                  'To change mode click the "Change to EOD"'
        bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
        return

    if user.position_in_menu == 4:
        message, reply_markup = build_menu(4, user)
        message = 'To submit bobtails use the following format(scac is assigned automatically):\n' \
                  'customer shift origin destination driver_name(no spaces) comment - 1 row per submission\n\n' \
                  'Customer/Shift - optional parameters, if not applied then the values from the buttons are used(click button to loop thru customer/carrier\n\n' \
                  'List of yards:\n' \
                  'AL: Alexander\nIGD: IGD\nTW: Taylor Way\nS1: Sumner 1\nS2: Sumner 2\nDP: DuPont\n\n' \
                  'Example:\n' \
                  'Target TW S2 driver_name comment is every word after the drivers name'
        bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
        return

    if user.position_in_menu == 5:
        message, reply_markup = build_menu(5)
        message = 'Paste a copy of the open moves log and watch the magic'
        bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
        return

    return


# All other messages handler
@bot.message_handler(content_types=['text'])
def messages(m):
    user = Users.query.filter_by(id=m.from_user.id).first()

    if not user:
        user = Users(id=m.from_user.id, name=m.from_user.first_name, position_in_menu=-1)
        bot.send_message(admin_bot_list[0],
                         'New User:\n' + m.from_user.first_name + '\n'
                         '`' + str(m.from_user.id) + '`',
                         parse_mode='MarkdownV2')
        db.session.add(user)
        db.session.commit()
        message = 'You are not registered, please request access from your manager\n\n' \
                  'Your id: `' + str(m.from_user.id) + '`(clickable), Give it to your manager\n\n' \
                  'This is the last time you get this message. The bot is for private ' \
                  'use only, so until you are granted access you will be ignored. Thank you.'
        bot.send_message(m.from_user.id, message, parse_mode='MarkdownV2')
        return

    if user.position_in_menu < 0:
        return

    if user.position_in_menu == 0:
        if m.text == 'EOD':
            message, reply_markup = build_menu(1)
            user.position_in_menu = 1
            db.session.commit()
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        if m.text == 'SORT':
            message, reply_markup = build_menu(5)
            user.position_in_menu = 5
            db.session.commit()
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        if m.text == 'BOBTAILS':
            message, reply_markup = build_menu(4, user=user)
            user.position_in_menu = 4
            db.session.commit()
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        if not is_bot_admin(m.from_user.id):
            return

        # row 1 - admin instruction
        # row 2 - user instruction is applied to
        # row 3 - additional details if required
        text = m.text.split('\n')

        if text[0] == 'list':
            user_list = Users.query.order_by(Users.position_in_menu).all()

            if len(user_list) == 0:
                bot.send_message(m.from_user.id, 'No users in the database(something has gone wrong)')
                return

            r = 'list of users:'
            for i in user_list:
                r += '\n' + str(i.id) + ' ' + i.name

            bot.send_message(m.from_user.id, r)
            return

        if text[0] == 'add':
            todo_user = Users.query.filter_by(id=int(text[1])).first()

            if todo_user:
                todo_user.position_in_menu = 0
                db.session.commit()
                bot.send_message(m.from_user.id, 'User successfully added')
            else:
                bot.send_message(m.from_user.id, 'User not found')
            return

        if text[0] == 'remove':
            todo_user = Users.query.filter_by(id=int(text[1])).first()

            if todo_user:
                todo_user.position_in_menu = -2
                db.session.commit()
                bot.send_message(m.from_user.id, 'User successfully removed')
            else:
                bot.send_message(m.from_user.id, 'User not found')
            return

        return

    # EOD report verification logic
    if user.position_in_menu == 1:
        if m.text == 'Back to main menu':
            message, reply_markup = build_menu(0, is_admin=is_bot_admin(m.from_user.id))
            user.position_in_menu = 0
            db.session.commit()
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        if m.text == 'Change to search':
            message, reply_markup = build_menu(2, is_admin=is_bot_admin(m.from_user.id))
            user.position_in_menu = 2
            db.session.commit()
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        if m.text == 'Current mode: "EOD"':
            bot.send_message(m.from_user.id, 'Just paste the info in "MOVE_ID CONTAINER_NUMBER MOVE_TYPE" format\n\n'
                                             'To update the completed moves log just upload the .csv file here.')
            return

        for i in EOD_logic_check(m.text):
            bot.send_message(m.from_user.id, i)
        return

    # Search in completed moves log logic
    if user.position_in_menu == 2:
        if m.text == 'Back to main menu':
            message, reply_markup = build_menu(0, is_admin=is_bot_admin(m.from_user.id))
            user.position_in_menu = 0
            db.session.commit()
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        if m.text == 'Change to EOD':
            message, reply_markup = build_menu(1, is_admin=is_bot_admin(m.from_user.id))
            user.position_in_menu = 1
            db.session.commit()
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        if m.text == 'Current mode: "SEARCH"':
            bot.send_message(m.from_user.id, 'Paste full MOVE_ID, last 4 of the MOVE_ID, or the CONTAINER_NUMBER to '
                                             'search for a match in the completed moves log\n\n'
                                             'To update the completed moves log just upload the .csv file here.')
            return

        res = search_for_an_ID_or_row(m.text)
        if res:
            for i in res:
                bot.send_message(m.from_user.id, i)
            return

        bot.send_message(m.from_user.id, 'Not found')
        return

    # BOBTAILS logic
    if user.position_in_menu == 4:
        if m.text == 'Back to main menu':
            user.position_in_menu = 0
            db.session.commit()
            message, reply_markup = build_menu(0, user=user)
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        if m.text == 'Customer: Target':
            user.current_customer = 'TJX'
            db.session.commit()
            message, reply_markup = build_menu(4, user=user)
            bot.send_message(m.from_user.id, 'Customer changed to TJX', reply_markup=reply_markup)
            return

        if m.text == 'Customer: TJX':
            user.current_customer = 'Yazaki'
            db.session.commit()
            message, reply_markup = build_menu(4, user=user)
            bot.send_message(m.from_user.id, 'Customer changed to Yazaki', reply_markup=reply_markup)
            return

        if m.text == 'Customer: Yazaki':
            user.current_customer = 'Target'
            db.session.commit()
            message, reply_markup = build_menu(4, user=user)
            bot.send_message(m.from_user.id, 'Customer changed to Target', reply_markup=reply_markup)
            return

        if m.text == 'Shift: AM':
            user.current_shift = 'PM'
            db.session.commit()
            message, reply_markup = build_menu(4, user=user)
            bot.send_message(m.from_user.id, 'Shift changed to PM', reply_markup=reply_markup)
            return

        if m.text == 'Shift: PM':
            user.current_shift = 'AM'
            db.session.commit()
            message, reply_markup = build_menu(4, user=user)
            bot.send_message(m.from_user.id, 'Shift changed to AM', reply_markup=reply_markup)
            return

        # Bobtail submission logic
        res = bobtail_filter_and_submit(m.text, user)
        if res:
            for i in res:
                bot.send_message(m.from_user.id, i)
            return

        bot.send_message(m.from_user.id, 'Nothing to submit, did you do everything right?')
        return

    # Sort Menu to sort current workload
    if user.position_in_menu == 5:
        if m.text == 'Back to main menu':
            message, reply_markup = build_menu(0, is_admin=is_bot_admin(m.from_user.id))
            user.position_in_menu = 0
            db.session.commit()
            bot.send_message(m.from_user.id, message, reply_markup=reply_markup)
            return

        res = split_sort_current_work(m.text)
        if res:
            for i in res:
                bot.send_message(m.from_user.id, i, parse_mode='MarkdownV2')
            return

        bot.send_message(m.from_user.id, 'Can\'t sort that')
        return

    return


@bot.message_handler(content_types=['document'])
def message_document(m):
    user = Users.query.filter_by(id=m.from_user.id).first()

    if not user:
        return

    if not (user.position_in_menu == 1 or user.position_in_menu == 2):
        return

    # Upload and verification of the file send to the bot
    try:
        file_path_unverified = 'temp/completed_moves_unverified.csv'
        web_file_info = bot.get_file(m.document.file_id)
        web_file = requests.get(f'https://api.telegram.org/file/bot{token}/{web_file_info.file_path}')
        file_unverified = open(file_path_unverified, 'w')
        file_unverified.write(web_file.text)
        file_unverified.close()
        print('File from user successfully uploaded to: "' + file_path_unverified + '"')

        file_unverified = open(file_path_unverified, 'r')
        csv_reader = csv.reader(file_unverified)
        header = next(csv_reader)

        if header != ['Month', 'Year Helper', 'Driver Name (Last, First)', 'Unique Move ID', 'Container Number',
                      'Inbound or Outbound', 'Load Status', 'Shift to move', 'Status', 'Created date']:
            bot.send_message(m.from_user.id, 'File error, Completed moves log has not been updated')
            return

        rows = []

        for i in csv_reader:
            rows.append(i)

        file_path_verified = 'temp/completed_moves_verified.csv'
        with open(file_path_verified, 'w') as file_verified:
            writer = csv.writer(file_verified)
            writer.writerow(header)
            writer.writerows(rows)

        file_unverified.close()

        bot.send_message(m.from_user.id, 'File updated succesfully')

    except Exception as e:
        print('Error uploading and saving the file')
        bot.send_message(m.from_user.id, 'Error uploading the file, please try again later or contact support')
        return
