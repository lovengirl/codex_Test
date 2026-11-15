from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[int] = mapped_column(primary_key=True, comment="고객 ID")
    name: Mapped[str] = mapped_column(Text, comment="고객 이름")
    email: Mapped[str | None] = mapped_column(Text, comment="이메일")
    phone: Mapped[str | None] = mapped_column(Text, comment="전화번호")

    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Employee(Base):
    __tablename__ = "employees"

    employee_id: Mapped[int] = mapped_column(primary_key=True, comment="직원 ID")
    name: Mapped[str] = mapped_column(Text, comment="직원 이름")
    department: Mapped[str] = mapped_column(Text, comment="소속 부서")

    orders: Mapped[list[Order]] = relationship(back_populates="employee")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("list_price >= 0", name="ck_products_list_price"),)

    product_id: Mapped[int] = mapped_column(primary_key=True, comment="상품 ID")
    sku: Mapped[str] = mapped_column(Text, unique=True, comment="상품 코드")
    name: Mapped[str] = mapped_column(Text, comment="상품명")
    list_price: Mapped[int] = mapped_column(comment="현재 정가(원)")

    orders: Mapped[list[Order]] = relationship(back_populates="product")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("date(order_date, '+0 days') IS order_date", name="ck_orders_date"),
        CheckConstraint("quantity >= 1", name="ck_orders_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_orders_unit_price"),
    )

    order_id: Mapped[int] = mapped_column(primary_key=True, comment="주문 ID")
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.customer_id"), comment="주문 고객 ID")
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.employee_id"), comment="담당 직원 ID")
    product_id: Mapped[int] = mapped_column(ForeignKey("products.product_id"), comment="주문 상품 ID")
    order_date: Mapped[str] = mapped_column(Text, comment="주문일(YYYY-MM-DD)")
    quantity: Mapped[int] = mapped_column(comment="주문 수량")
    unit_price: Mapped[int] = mapped_column(comment="주문 당시 판매 단가(원)")

    customer: Mapped[Customer] = relationship(back_populates="orders")
    employee: Mapped[Employee] = relationship(back_populates="orders")
    product: Mapped[Product] = relationship(back_populates="orders")
