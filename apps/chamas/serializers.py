from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.serializers import PhoneField

from .models import OFFICIAL_ROLES, BankAccount, Chama, Invite, Membership


class CreateChamaSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, min_length=2)
    type = serializers.ChoiceField(choices=Chama.Type.choices)
    founder_role = serializers.ChoiceField(choices=list(OFFICIAL_ROLES) + ["member"])
    expected_members = serializers.IntegerField(min_value=0, max_value=10_000, default=0)
    meets = serializers.ChoiceField(choices=Chama.Meets.choices, default=Chama.Meets.MONTHLY)


class BankAccountSerializer(serializers.ModelSerializer):
    account_no = serializers.CharField(write_only=True, max_length=40)
    account_no_last4 = serializers.CharField(read_only=True)

    class Meta:
        model = BankAccount
        fields = ["bank", "account_name", "account_no", "account_no_last4", "paybill", "till",
                  "statement_route"]


class ChamaSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    my_member_code = serializers.SerializerMethodField()
    i_am_founder = serializers.SerializerMethodField()
    statement_inbox = serializers.CharField(source="statement_inbox.address", read_only=True)
    bank_account = serializers.SerializerMethodField()

    class Meta:
        model = Chama
        fields = ["id", "name", "type", "state", "mode", "member_code_prefix",
                  "expected_members", "meets", "created_at", "setup_expires_at",
                  "activated_at", "my_role", "my_member_code", "i_am_founder", "statement_inbox",
                  "bank_account"]

    def _mine(self, obj):
        return self.context.get("memberships", {}).get(obj.id)

    def get_my_role(self, obj) -> str | None:
        m = self._mine(obj)
        return m.role if m else None

    def get_my_member_code(self, obj) -> str | None:
        m = self._mine(obj)
        return m.member_code if m else None

    def get_i_am_founder(self, obj) -> bool:
        m = self._mine(obj)
        return bool(m) and m.user_id == obj.created_by_id

    @extend_schema_field(BankAccountSerializer(allow_null=True))
    def get_bank_account(self, obj):
        account = obj.bank_accounts.filter(active_to__isnull=True).first()
        return BankAccountSerializer(account).data if account else None


class RuleBookSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["draft", "in_force"], required=False)
    version_no = serializers.IntegerField(allow_null=True, required=False)
    effective_from = serializers.DateField(allow_null=True, required=False)
    hash = serializers.CharField(allow_null=True, required=False)
    rules = serializers.JSONField()
    book = serializers.JSONField(help_text='{"en": [{"ref", "text"}], "sw": [...]}')


class InviteDetailSerializer(serializers.Serializer):
    chama = serializers.JSONField()
    role = serializers.CharField()
    book = serializers.JSONField()


class PositionSerializer(serializers.Serializer):
    member_code = serializers.CharField()
    contributed_cents = serializers.IntegerField()
    opening_cents = serializers.IntegerField()
    pending_cash_cents = serializers.IntegerField()
    arrears_cents = serializers.IntegerField()
    fines_cents = serializers.IntegerField()
    credit_cents = serializers.IntegerField()
    next_due_period = serializers.CharField(allow_null=True)
    next_due_cents = serializers.IntegerField()


class MembershipSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="user.display_name")

    class Meta:
        model = Membership
        fields = ["id", "member_code", "name", "role", "state", "is_founding", "joined_on",
                  "rules_adopted_at"]


class InviteCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.Role.choices, default="member")
    phone = PhoneField(required=False)
    by_sms = serializers.BooleanField(default=False, help_text="For someone without the app: "
                                      "the invite goes by SMS; they reply YES <code>.")
    language = serializers.ChoiceField(choices=["en", "sw"], default="en")


class InviteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invite
        fields = ["id", "code", "role", "phone_hint", "state", "expires_at"]
