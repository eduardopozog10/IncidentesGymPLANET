from django import template
from incidents.presentation import format_quantity

register = template.Library()
register.filter('request_quantity', format_quantity)
