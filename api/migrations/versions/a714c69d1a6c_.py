"""empty message

Revision ID: a714c69d1a6c
Revises: b7006cbdd923
Create Date: 2020-01-31 22:41:55.328246

"""

# revision identifiers, used by Alembic.
revision = 'a714c69d1a6c'
down_revision = 'b7006cbdd923'

from alembic import op
import sqlalchemy as sa


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('organisation', sa.Column('email_from', sa.String(length=100), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('organisation', 'email_from')
    # ### end Alembic commands ###
